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

def extract_cover_image(comic_file_path):
    try:
        if comic_file_path.lower().endswith('.cbz'):
            with zipfile.ZipFile(comic_file_path, 'r') as archive:
                image_files = sorted([f for f in archive.namelist() if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                if image_files:
                    with archive.open(image_files[0]) as image_file:
                        return Image.open(image_file)
        elif comic_file_path.lower().endswith('.cbr'):
            with RarFile(comic_file_path, 'r') as archive:
                image_files = sorted([f for f in archive.namelist() if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                if image_files:
                    with archive.open(image_files[0]) as image_file:
                        return Image.open(image_file)
    except Exception as e:
        print(f"Error extracting cover from {comic_file_path}: {e}")
    return None

def identify_comic(comic_file_path, cover_image):
    guess = guessit.guessit(comic_file_path)
    series_title = guess.get('title')
    issue_number = guess.get('issue')

    if cover_image:
        cover_hash = imagehash.phash(cover_image)
        print(f"  Cover hash: {cover_hash}")

    if series_title and issue_number:
        print(f"  Guessed Series: {series_title}, Issue: {issue_number}")
        return search_comicvine(series_title, issue_number)
    else:
        print("  Could not guess series and issue from filename.")
        return None

def search_comicvine(series_title, issue_number):
    """
    Searches Comic Vine for a specific comic issue.
    """
    if not COMICVINE_API_KEY:
        print("  Comic Vine API key is not set. Skipping search.")
        return None

    print(f"  Searching Comic Vine for '{series_title}' issue #{issue_number}...")

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

        # For now, let's just take the first result.
        # In a real application, you might want to be smarter about this.
        volume = results[0]
        volume_name = volume.get('name')
        volume_id = volume.get('id')
        print(f"  Found volume: '{volume_name}' (ID: {volume_id})")

        # Now, get the issues for that volume
        volume_url = f"https://comicvine.gamespot.com/api/volume/{volume_id}/"
        params = {
            "api_key": COMICVINE_API_KEY,
            "format": "json",
        }
        response = requests.get(volume_url, params=params, headers=headers)
        response.raise_for_status()

        issues = response.json().get('results', {}).get('issues', [])
        if not issues:
            print(f"  No issues found for volume '{volume_name}'.")
            return None

        # Find the matching issue
        for issue in issues:
            if issue.get('issue_number') == str(issue_number):
                print(f"  Found issue: {issue.get('name')} ({issue.get('id')})")
                return issue

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
    year = issue_details.get('cover_date', 'unknown').split('-')[0] if issue_details.get('cover_date') else 'unknown'

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

    for comic_file in comic_files:
        print(f"Processing {comic_file}...")
        cover_image = extract_cover_image(comic_file)
        if cover_image:
            print(f"  Successfully extracted cover image.")
            issue_details = identify_comic(comic_file, cover_image)
            organize_file(comic_file, issue_details, args.output_dir, args.dry_run)
        else:
            print(f"  Could not extract cover image.")

if __name__ == '__main__':
    main()