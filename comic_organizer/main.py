import argparse
import os
from dotenv import load_dotenv
import guessit
import imagehash
import requests
import zipfile
from rarfile import RarFile
from PIL import Image

COMICVINE_API_KEY = ""

def scan_comic_files(input_dir):
    comic_files = []
    for root, _, files in os.walk(input_dir):
        for file in files:
            if file.lower().endswith(('.cbz', '.cbr')):
                comic_files.append(os.path.join(root, file))
    return comic_files

import io

def extract_cover_image(comic_file_path):
    try:
        image_data = None
        if comic_file_path.lower().endswith('.cbz'):
            with zipfile.ZipFile(comic_file_path, 'r') as archive:
                image_files = sorted([f for f in archive.namelist() if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                if image_files:
                    with archive.open(image_files[0]) as image_file:
                        image_data = image_file.read()
        elif comic_file_path.lower().endswith('.cbr'):
            with RarFile(comic_file_path, 'r') as archive:
                image_files = sorted([f for f in archive.namelist() if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                if image_files:
                    with archive.open(image_files[0]) as image_file:
                        image_data = image_file.read()
        
        if image_data:
            return Image.open(io.BytesIO(image_data))

    except Exception as e:
        print(f"Error extracting cover from {comic_file_path}: {e}")
    return None

import re

def identify_comic(comic_file_path, cover_image, series_cache):
    file_name = os.path.basename(comic_file_path)
    folder_name = os.path.basename(os.path.dirname(comic_file_path))
    folder_path = os.path.dirname(comic_file_path)

    # Extract series title and year from folder name
    match = re.match(r'(.*?)\s*\((\d{4})\)', folder_name)
    if match:
        series_title = match.group(1).strip()
        series_year = match.group(2)
    else:
        series_title = folder_name
        series_year = None

    # --- New Heuristic-Based Issue Number Extraction ---
    issue_number = None
    
    # 1. Prioritize numbers prefixed with '#'
    hash_match = re.search(r'#(\d+)', file_name)
    if hash_match:
        issue_number = hash_match.group(1)
    else:
        # 2. Find all standalone numbers in the filename
        potential_numbers = re.findall(r'\b\d+\b', file_name)
        
        # 3. Filter out likely years
        non_year_numbers = [
            num for num in potential_numbers 
            if not (
                (len(num) == 4 and (num.startswith('19') or num.startswith('20'))) or
                (series_year and num == series_year)
            )
        ]
        
        # 4. Select the last remaining number
        if non_year_numbers:
            issue_number = non_year_numbers[-1]

    # 5. Fallback to guessit if the new logic fails
    if not issue_number:
        guess = guessit.guessit(file_name)
        print(f"  Guessit result: {guess}")
        issue_number = guess.get('issue') or guess.get('episode')

    if cover_image:
        cover_hash = imagehash.phash(cover_image)
        print(f"  Cover hash: {cover_hash}")

    if series_title and issue_number:
        print(f"  Guessed Series: {series_title}, Issue: {issue_number}")
        
        selected_volume = series_cache.get(folder_path)
        if selected_volume is None:
            selected_volume = select_series(series_title, series_year)
            series_cache[folder_path] = selected_volume
        
        if selected_volume:
            return search_issue(selected_volume, issue_number)
        else:
            return None
    else:
        print("  Could not guess series and issue from filename.")
        return None

def select_series(series_title, series_year=None):
    """
    Searches for a series and prompts the user to select from the results.
    """
    if not COMICVINE_API_KEY:
        print("  Comic Vine API key is not set. Skipping search.")
        return None

    print(f"  Searching Comic Vine for series '{series_title}' (Year: {series_year or 'Any'})...")

    # Search for the volume (series)
    search_url = "https://comicvine.gamespot.com/api/search/"
    params = {
        "api_key": COMICVINE_API_KEY,
        "format": "json",
        "query": series_title,
        "resources": "volume",
    }
    headers = {
        "User-Agent": "ComicOrganizer/1.0"
    }

    try:
        response = requests.get(search_url, params=params, headers=headers)
        response.raise_for_status()  # Raise an exception for bad status codes

        results = response.json().get('results', [])
        if not results:
            print(f"  No results found for series '{series_title}'.")
            return None

        volume = None
        if len(results) > 1:
            print("  Multiple series found. Please select one:")
            for i, res in enumerate(results):
                print(f"    {i+1}: {res.get('name')} ({res.get('start_year')}) - {res.get('site_detail_url')}")
            print(f"    {len(results)+1}: None of the above")

            while True:
                try:
                    choice = int(input("  Enter your choice: "))
                    if 1 <= choice <= len(results):
                        volume = results[choice-1]
                        break
                    elif choice == len(results) + 1:
                        return None
                    else:
                        print("  Invalid choice. Please try again.")
                except ValueError:
                    print("  Invalid input. Please enter a number.")
        elif results:
            volume = results[0]
        
        return volume

    except requests.exceptions.RequestException as e:
        print(f"  Error searching Comic Vine: {e}")
        return None

def search_issue(volume, issue_number):
    """
    Searches for a specific issue within a given volume.
    """
    if not volume:
        return None

    volume_name = volume.get('name')
    volume_id = volume.get('id')
    print(f"  Searching for issue #{issue_number} in volume '{volume_name}' (ID: {volume_id})...")

    # Get the issues for that volume
    volume_url = f"https://comicvine.gamespot.com/api/volume/4050-{volume_id}/"
    params = {
        "api_key": COMICVINE_API_KEY,
        "format": "json",
        "field_list": "issues"
    }
    headers = {
        "User-Agent": "ComicOrganizer/1.0"
    }

    try:
        response = requests.get(volume_url, params=params, headers=headers)
        response.raise_for_status()

        volume_details = response.json().get('results', {})
        issues = volume_details.get('issues', [])
        if not issues:
            print(f"  No issues found for volume '{volume_name}'.")
            return None

        # Find the matching issue
        for issue in issues:
            try:
                if issue.get('issue_number') and int(issue.get('issue_number')) == int(issue_number):
                    # Now fetch the full issue details
                    issue_url = f"https://comicvine.gamespot.com/api/issue/4000-{issue.get('id')}/"
                    params = {
                        "api_key": COMICVINE_API_KEY,
                        "format": "json",
                    }
                    issue_response = requests.get(issue_url, params=params, headers=headers)
                    issue_response.raise_for_status()
                    issue_details = issue_response.json().get('results')
                    if issue_details:
                        print(f"  Found issue: {issue_details.get('name')} ({issue_details.get('id')})")
                        return issue_details
            except (ValueError, TypeError):
                # Ignore if issue numbers are not valid integers
                continue

        print(f"  No matching issue found for issue number {issue_number}.")
        return None

    except requests.exceptions.RequestException as e:
        print(f"  Error searching Comic Vine: {e}")
        return None

def organize_file(original_path, issue_details, output_dir, dry_run=False):
    if not issue_details:
        return

    volume_name = issue_details.get('volume', {}).get('name')
    issue_number = issue_details.get('issue_number')
    # Use the year from the folder name if available, otherwise fall back to cover_date
    year = issue_details.get('volume', {}).get('start_year') or (issue_details.get('cover_date', 'unknown').split('-')[0] if issue_details.get('cover_date') else 'unknown')

    if not all([volume_name, issue_number]):
        print("  Could not determine new file name. Missing volume name or issue number.")
        return

    _, extension = os.path.splitext(original_path)
    new_series_folder = os.path.join(output_dir, f"{volume_name} ({year})")
    new_file_name = f"{volume_name} - Issue #{issue_number}{extension}"
    new_file_path = os.path.join(new_series_folder, new_file_name)

    if dry_run:
        print(f"  [DRY RUN] Would move and rename to: {new_file_path}")
    else:
        print(f"  Moving and renaming to: {new_file_path}")
        os.makedirs(new_series_folder, exist_ok=True)
        os.rename(original_path, new_file_path)

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Organize comic book files.')
    parser.add_argument('input_dir', help='The directory containing the unorganized comic files.')
    parser.add_argument('output_dir', help='The directory to store the organized comic files.')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without moving files.')
    args = parser.parse_args()

    global COMICVINE_API_KEY
    COMICVINE_API_KEY = os.getenv("COMICVINE_API_KEY")

    comic_files = scan_comic_files(args.input_dir)
    print(f"Found {len(comic_files)} comic files.")

    # Group comics by parent directory
    comics_by_folder = {}
    for comic_file in comic_files:
        folder = os.path.dirname(comic_file)
        if folder not in comics_by_folder:
            comics_by_folder[folder] = []
        comics_by_folder[folder].append(comic_file)

    series_cache = {}

    for folder, comics in comics_by_folder.items():
        print(f"\nProcessing folder: {folder}")
        selected_volume = None
        for comic_file in comics:
            print(f"Processing {comic_file}...")
            cover_image = extract_cover_image(comic_file)
            if cover_image:
                print(f"  Successfully extracted cover image.")
                issue_details = identify_comic(comic_file, cover_image, series_cache)
                organize_file(comic_file, issue_details, args.output_dir, args.dry_run)
            else:
                print(f"  Could not extract cover image.")


if __name__ == '__main__':
    main()