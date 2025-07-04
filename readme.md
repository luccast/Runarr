# RUNARR
   ______     __  __     __   __     ______     ______     ______
  /\  == \   /\ \/\ \   /\ "-.\ \   /\  __ \   /\  == \   /\  == \
  \ \  __<   \ \ \_\ \  \ \ \-.  \  \ \  __ \  \ \  __<   \ \  __<
   \ \_\ \_\  \ \_____\  \ \_\\"\_\  \ \_\ \_\  \ \_\ \_\  \ \_\ \_\
    \/_/ /_/   \/_____/   \/_/ \/_/   \/_/\/_/   \/_/ /_/   \/_/ /_/

A tool to organize comic book archives (CBZ/CBR) by fetching metadata from Comic Vine and renaming/moving files accordingly.

## Features
- Organizes `.cbz` and `.cbr` comic files into structured folders
- Fetches metadata from Comic Vine
- Renames files and folders based on series, issue, and date
- Converts `.cbr` files to `.cbz` automatically
- Embeds `ComicInfo.xml` metadata into `.cbz` files
- Handles extra files and places them in an `Extras` folder
- Supports dry-run mode for safe testing

## Requirements
- Python 3.7+
- [Comic Vine API Key](https://comicvine.gamespot.com/api/)

## Installation

1. **Clone the repository:**
   ```sh
   git clone <your-repo-url>
   cd <your-repo-directory>
   ```

2. **Install dependencies:**
   You can use either `pip` with the provided requirements file, or install as a project:
   ```sh
   pip install -r comic_organizer/requirements.txt
   # OR
   pip install .
   ```

3. **Set up your Comic Vine API key:**
   - Create a `.env` file in the project root (or wherever you run the script) with:
     ```env
     COMICVINE_API_KEY=your_api_key_here
     ```

## Usage

You can run the tool using the installed script or directly via Python:

### As a CLI tool (after `pip install .`):
```sh
runarr <input_dir> [output_dir] [--series-folder SERIES] [--dry-run]
```

### Or directly:
```sh
python -m comic_organizer.main <input_dir> [output_dir] [--series-folder SERIES] [--dry-run]
```

#### Arguments
- `<input_dir>`: Directory containing your comic files (CBZ/CBR)
- `[output_dir]`: (Optional) Directory to store organized files (defaults to in-place)
- `--series-folder SERIES`: (Optional) Only process a specific series folder within the input directory
- `--dry-run`: Perform a dry run without moving or renaming files

#### Example
```sh
runarr /path/to/comics /path/to/organized --dry-run
```

## Notes
- All `.cbr` files are automatically converted to `.cbz` before processing.
- Extra files in comic folders are moved to an `Extras` subfolder.
- The tool requires an internet connection to fetch metadata from Comic Vine.

## License
MIT
