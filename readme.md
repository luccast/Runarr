# RUNARR

```
   ______     __  __     __   __     ______     ______     ______
  /\  == \   /\ \/\ \   /\ "-.\ \   /\  __ \   /\  == \   /\  == \
  \ \  __<   \ \ \_\ \  \ \ \-.  \  \ \  __ \  \ \  __<   \ \  __<
   \ \_\ \_\  \ \_____\  \ \_\\"\_\  \ \_\ \_\  \ \_\ \_\  \ \_\ \_\
    \/_/ /_/   \/_____/   \/_/ \/_/   \/_/\/_/   \/_/ /_/   \/_/ /_/
```

A tool to organize comic book runs in archive (CBZ/CBR) format by fetching metadata from Comic Vine and renaming/moving files accordingly.

## Features
- Organizes `.cbz` and `.cbr` comic files into structured folders optmized for use with Mylar3, Kavita, Komga
- Fetches metadata from Comic Vine with built in automated rate limiting system
- Select from matched series with link to Comic Vine Series
- Renames files and folders based on series, issue, and date
- Converts `.cbr` files to `.cbz` automatically, with a progress bar for large files.
- Embeds `ComicInfo.xml` metadata into `.cbz` files
- Handles extra files and places them in an `Extras` folder
- Supports dry-run mode for safe testing
- Interactive progress bars for a better user experience.

## Requirements
- Python 3.7+
- [Comic Vine API Key](https://comicvine.gamespot.com/api/)
- For .cbr file support: `unrar` (or equivalent) must be installed and available in your system PATH. On macOS, you can install it with `brew install unar`.

## Installation

1. **Clone the repository:**
   ```sh
   git clone https://github.com/luccast/Runarr.git
   cd Runarr
   ```

2. **Install dependencies:**
   You can use either `pip` with the provided requirements file, or install as a project:
   ```sh
   pip install -r comic_organizer/requirements.txt
   # OR
   pip install .
   ```

3. **Set up your Comic Vine API key:**
   - Run the program the first time with `--comicvine-api-key "yourkey"` to save it in your local home directory.
   - Alternatively, create a `.env` file in the project root (or wherever you run the script) with:
     ```env
     COMICVINE_API_KEY=your_api_key_here
     ```

## Usage

### Naming your series folder is the first key step

Ensure they are named like this:
```SeriesName Version(Optional) (Year)```
Examples:
```Amazing X-Men (2014)```
```Batman Beyond v3 (2016)```

You can run the tool using the installed script or directly via Python:

### As a CLI tool (after `pip install .`):
```sh
runarr [input_dir] [output_dir] [options]
```

### Or directly:
```sh
python -m comic_organizer.main [input_dir] [output_dir] [options]
```

#### Arguments
- `input_dir`: (Optional) Directory containing your comic files (CBZ/CBR). Defaults to the current directory if not specified.
- `output_dir`: (Optional) Directory to store organized files (defaults to in-place)
- `--series-folder SERIES`: (Optional) Only process a specific series folder within the input directory
- `--dry-run`: Perform a dry run without moving or renaming files
- `--force-refresh`: Force a refresh of cached data for a specific series folder.
- `-o`, `--overwrite`: Treat issues as if they have no metadata, forcing a re-download and overwrite.
- `-y`, `--yes`: Automatically answer yes to all prompts and skip confirmations.
- `--comicvine-api-key`: Set or update your Comic Vine API key. This will be saved for future use.

#### Example
```sh
runarr --dry-run
runarr /path/to/comics /path/to/organized --dry-run
```

## Notes
- All `.cbr` files are automatically converted to `.cbz` before processing.
- Extra files in comic folders are moved to an `Extras` subfolder.
- The tool requires an internet connection to fetch metadata from Comic Vine.

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License
[MIT](https://choosealicense.com/licenses/mit/)