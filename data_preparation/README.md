# Thorax DICOM Organizer

This tool copies DICOM scan folders from the Sectra drive into three folders:

- `Z:\thorax_data\pneumothorax`
- `Z:\thorax_data\haemothorax`
- `Z:\thorax_data\joint`

The spreadsheet must contain `scan_dir` and `thorax_condition` columns. The following condition values are recognized:

| Spreadsheet value | Destination folder |
| --- | --- |
| `pt` | `pneumothorax` |
| `ht` | `haemothorax` |
| `htpt_joint` | `joint` |

Entries such as `#N/A`, `error`, blank values, invalid paths, and source folders that do not exist are skipped and written to a timestamped report in `Z:\thorax_data`.

## Run on the VM

1. Clone or pull the repository on the VM.
2. Install the Excel reader once: `py -3 -m pip install -r data_preparation\requirements.txt`
3. Double-click `data_preparation\RUN_THORAX_DICOM_ORGANIZER.bat`.
4. Select the spreadsheet when its file-selection window opens.

The default source path is `Z:\sectra2`. If the actual scan root is `Z:\sectra`, run this from a Command Prompt instead:

```bat
py -3 data_preparation\organize_thorax_dicoms.py "C:\path\to\spreadsheet.xlsx" --source-root "Z:\sectra"
```

For a scan directory such as `000672/000044/`, the DICOM files are copied from `Z:\sectra2\000672\000044` to the matching condition folder while keeping the same directory structure.
