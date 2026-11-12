"""IMPACT EXP-002: pretrained SwinUNETR for 3D pneumothorax segmentation.

Requires preprocessed 2.5-mm npz cache from EXP-001 and its patient_split.json.
No held-out test evaluation: preserve it for a later, pre-agreed comparison.

Example on GPU Windows VM:
  python impact_pt_swinunetr.py --mode check
  python impact_pt_swinunetr.py --mode smoke
  python impact_pt_swinunetr.py --mode train --epochs 80

The --mode check and --mode smoke commands must be run before training.
"""
from __future__ import annotations
import argparse
import csv
import json
import random
import time
import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np
import torch
from monai.losses import DiceCELoss
from monai.inferers import sliding_window_inference
from monai.networks.nets import SwinUNETR

WORK = Path(r"D:\IMPACT_PT\cache_2p5mm")
SPLIT = Path(r"Z:\Model_data\PT_model_experiments\EXP001_SegResNet\patient_split.json")
OUTPUT = Path(r"Z:\Model_data\PT_model_experiments\EXP002_SwinUNETR_Pretrained")
# Transfer official MONAI SSL backbone model_swinvit.pt to this location.
WEIGHTS = Path(r"Z:\Model_data\pretrained_models\model_swinvit.pt")
SEED = 20261110
PATCH = (64, 64, 64)  # Z, Y, X; multiple of 32 for SwinUNETR.
FEATURE_SIZE = 48  # Required for official 5050-CT SSL checkpoint.


def load_cases():
    if not SPLIT.exists():
        raise FileNotFoundError(f"Missing split: {SPLIT}. Run EXP-001 preparation first.")
    pairs = json.loads(SPLIT.read_text(encoding="utf-8"))
    if not isinstance(pairs, dict) or not pairs:
        raise ValueError("Unexpected or empty split JSON")
    names = {phase: sorted(k for k,v in pairs.items() if v==phase)
             for phase in ("train","val","test")}
    for phase in names:
        if not names[phase]:
            raise ValueError(f"No {phase} cases in split")
    patients={phase: {case.split('-')[0] for case in cases} for phase,cases in names.items()}
    for a,b in (("train","val"),("train","test"),("val","test")):
        if patients[a] & patients[b]:
            raise ValueError(f"Patient overlap in {a}/{b}")
    for case in sum(names.values(), []):
        if not (WORK / f"{case}.npz").exists():
            raise FileNotFoundError(f"Missing preprocessed case: {WORK / (case+'.npz')}")
    return names


class NPZCache:
    def __init__(self, limit=2):
        self.items=OrderedDict()
        self.limit=limit
    def get(self, case):
        if case in self.items:
            self.items.move_to_end(case)
            return self.items[case]
        with np.load(WORK / f"{case}.npz") as d:
            image=d["image"].astype(np.float32)
            label=d["label"].astype(np.uint8)
        if image.ndim!=3 or label.shape!=image.shape or not np.all(np.isfinite(image)):
            raise ValueError(f"Invalid cached CT/label shapes for {case}")
        if not np.isin(np.unique(label), [0,1]).all():
            raise ValueError(f"Nonbinary label for {case}")
        self.items[case]=(image,label)
        if len(self.items)>self.limit:
            self.items.popitem(last=False)
        return image,label


def patch_from_case(image, mask, rng):
    sz=np.array(PATCH)
    pads=[(0,max(0,int(s-v))) for v,s in zip(image.shape,sz)]
    if any(p[1] for p in pads):
        image=np.pad(image,pads,mode="constant",constant_values=0)
        mask=np.pad(mask,pads,mode="constant",constant_values=0)
    shape=np.array(image.shape)
    if mask.any() and rng.random()<.7:
        fg=np.argwhere(mask>0)
        center=fg[rng.randrange(len(fg))]
        starts=[int(np.clip(center[d]-rng.randrange(int(sz[d])),0,shape[d]-sz[d])) for d in range(3)]
    else:
        starts=[rng.randrange(int(shape[d]-sz[d])+1) for d in range(3)]
    sub=tuple(slice(s,s+int(p)) for s,p in zip(starts,sz))
    x=image[sub].copy()
    y=mask[sub].copy()
    for d in range(3):
        if rng.random()<.5:
            x=np.flip(x,axis=d).copy()
            y=np.flip(y,axis=d).copy()
    if rng.random()<.5:
        x=np.clip(x*rng.uniform(.9,1.1)+rng.uniform(-.03,.03),0,1)
    return (torch.from_numpy(np.ascontiguousarray(x))[None,None].float(),
            torch.from_numpy(np.ascontiguousarray(y))[None,None].float())


def make_model():
    return SwinUNETR(in_channels=1, out_channels=1, feature_size=FEATURE_SIZE,
                     use_checkpoint=True, spatial_dims=3)


def load_ssl(model):
    if not WEIGHTS.is_file():
        raise FileNotFoundError(
            f"Missing official self-supervised checkpoint: {WEIGHTS}\n"
            "Download model_swinvit.pt from the official MONAI release, then copy to Z:.")
    print(f"[PRETRAIN] Reading {WEIGHTS} ({WEIGHTS.stat().st_size/1e6:.1f} MB)",flush=True)
    ckpt=torch.load(WEIGHTS, map_location="cpu", weights_only=True)
    if not isinstance(ckpt,dict) or not isinstance(ckpt.get("state_dict"),dict):
        raise ValueError("Checkpoint missing expected state_dict; wrong pretrained file?")
    # Official MONAI SwinUNETR.load_from copies the matching SSL encoder weights.
    model.load_from(ckpt)
    print("[PRETRAIN] Official MONAI load_from completed",flush=True)
    # Verify actual weights changed vs random initialisation, especially encoder.
    # Avoid relying on load_from success message alone if upstream MONAI changes.


def checkpoint_pretrain_verified(model):
    initial=model.swinViT.patch_embed.proj.weight.detach().cpu().clone()
    load_ssl(model)
    after=model.swinViT.patch_embed.proj.weight.detach().cpu()
    if torch.equal(initial, after):
        raise RuntimeError("PRETRAIN FAILED: initial and loaded patch-embedding weights identical")
    print("[PRETRAIN] Encoder patch-embedding tensor changed: verified",flush=True)


def evaluate(model, cases, cache, device, limit=None):
    model.eval()
    scores=[]
    selected=cases if limit is None else cases[:limit]
    with torch.inference_mode():
        for i,case in enumerate(selected,1):
            image,label=cache.get(case)
            # Full-volume sliding-window inference, output kept on CPU to save VRAM.
            x=torch.from_numpy(image.copy())[None,None]
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                out=sliding_window_inference(x, PATCH, 1, model,
                                             overlap=.25, sw_device=device,
                                             device=torch.device("cpu"))
            pred=(torch.sigmoid(out[0,0].float())>.5).numpy()
            gt=label.astype(bool)
            denom=int(pred.sum())+int(gt.sum())
            dice=2*int(np.logical_and(pred,gt).sum())/denom if denom else 1.0
            row={"case":case,"dice":round(dice,6),"gt_voxels":int(gt.sum()),"pred_voxels":int(pred.sum())}
            scores.append(row)
            print(f"[VAL {i}/{len(selected)}] {case} Dice={dice:.4f} gt={row['gt_voxels']} pred={row['pred_voxels']}",flush=True)
            del x,out
    return scores


def write_rows(path, rows, append=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(rows[0].keys())
    with path.open("a" if append else "w", newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        if not append or f.tell()==0:
            writer.writeheader()
        writer.writerows(rows)


def train(names, mode, epochs, steps, lr):
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    device=torch.device("cuda")
    cache=NPZCache()
    print(f"[GPU] {torch.cuda.get_device_name(0)}, VRAM={torch.cuda.get_device_properties(0).total_memory/2**30:.1f} GiB")
    model=make_model()
    checkpoint_pretrain_verified(model)
    model=model.to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=1e-5)
    criterion=DiceCELoss(sigmoid=True,lambda_dice=1,lambda_ce=1)
    # GPU AMP reduces activation footprint. GradScaler is compatible with current PyTorch.
    scaler=torch.amp.GradScaler("cuda")
    smoke=mode=="smoke"
    if smoke:
        epochs,steps=1,2
    OUTPUT.mkdir(parents=True,exist_ok=True)
    rng=random.Random(SEED)
    latest=OUTPUT / "latest_model.pt"
    bestfile=OUTPUT / "best_model.pt"
    start=1
    best=-1.0
    if not smoke and latest.exists():
        state=torch.load(latest,map_location="cpu",weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scaler.load_state_dict(state["scaler"])
        start=int(state["epoch"])+1
        best=float(state["best_val_dice"])
        rng.setstate(state["rng_state"])
        torch.set_rng_state(state["torch_rng"])
        if "cuda_rng" in state:
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        print(f"[RESUME] epoch {start} best={best:.4f}",flush=True)
    print(f"[MODE] {mode}; train={len(names['train'])}, val={len(names['val'])}, test={len(names['test'])} (test never accessed)")
    for epoch in range(start,epochs+1):
        t0=time.time()
        model.train()
        losses=[]
        for st in range(steps):
            case=rng.choice(names["train"])
            image,label=cache.get(case)
            x,y=patch_from_case(image,label,rng)
            x,y=x.to(device),y.to(device)
            optimizer.zero_grad(set_to_none=True)
            try:
                with torch.autocast(device_type="cuda",dtype=torch.float16):
                    logits=model(x)
                    loss=criterion(logits,y)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            except torch.cuda.OutOfMemoryError:
                print("[OOM] 12-GiB GPU exhausted. Try --mode smoke after closing other GPU processes; don't enlarge PATCH.")
                raise
            losses.append(float(loss.detach().cpu()))
            if smoke or (st+1)%5==0:
                print(f"[TRAIN epoch={epoch} step={st+1}/{steps}] loss={losses[-1]:.4f}",flush=True)
        scores=evaluate(model,names["val"],cache,device,limit=1 if smoke else None)
        val=float(np.mean([r["dice"] for r in scores]))
        print(f"[EPOCH {epoch}/{epochs}] mean_loss={np.mean(losses):.4f} val_dice={val:.4f} sec={time.time()-t0:.0f}",flush=True)
        if not smoke:
            state={"epoch":epoch,"model":model.state_dict(),"optimizer":optimizer.state_dict(),
                   "scaler":scaler.state_dict(),"best_val_dice":max(best,val),
                   "rng_state":rng.getstate(),"torch_rng":torch.get_rng_state(),
                   "cuda_rng":torch.cuda.get_rng_state_all()}
            torch.save(state,latest)
            if val>best:
                best=val
                torch.save({"epoch":epoch,"model":model.state_dict(),"val_dice":val},bestfile)
                write_rows(OUTPUT / "best_val_per_case.csv",scores)
                print(f"[BEST] saved: {bestfile}",flush=True)
            write_rows(OUTPUT / "train_history.csv",
                       [{"epoch":epoch,"train_loss":round(float(np.mean(losses)),6),"val_dice":round(val,6)}],append=True)
    if smoke:
        print("[SUCCESS] EXP-002 smoke test passed. No trained checkpoint or test result saved.")
    else:
        print("[DONE] EXP-002 training complete. Test cases were NOT evaluated.")
        print(f"[OUTPUT] {OUTPUT}")


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode",choices=["check","smoke","train"],default="check")
    p.add_argument("--epochs",type=int,default=80)
    p.add_argument("--steps-per-epoch",type=int,default=24)
    p.add_argument("--lr",type=float,default=1e-4)
    args=p.parse_args()
    if args.epochs<1 or args.steps_per_epoch<1: p.error("Epochs and steps must be >= 1")
    names=load_cases()
    print(f"[DATA] D: cache={WORK}, split={SPLIT}")
    print(f"[SPLIT] train={len(names['train'])} val={len(names['val'])} test={len(names['test'])} (held out)")
    if args.mode=="check":
        if not torch.cuda.is_available():
            raise RuntimeError("GPU unavailable")
        model=make_model()
        checkpoint_pretrain_verified(model)
        print("[CHECK OK] GPU, prepared files, patient split and pretrained encoder are available.")
        print("Next: run --mode smoke")
    else:
        train(names,args.mode,args.epochs,args.steps_per_epoch,args.lr)

if __name__=="__main__":
    main()
