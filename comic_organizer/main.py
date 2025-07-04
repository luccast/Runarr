import argparse
import os
from dotenv import load_dotenv
import guessit
import imagehash
import requests
import zipfile
from rarfile import RarFile
from PIL import Image
from datetime import datetime
import xml.etree.ElementTree as ET
import tempfile
import shutil

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

def identify_comic(comic_file_path, cover_image, series_cache, volume_issues_cache):
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
        
        # Step 1: Get the selected volume (cached per folder)
        selected_volume = series_cache.get(folder_path)
        if selected_volume is None:
            selected_volume = select_series(series_title, series_year)
            series_cache[folder_path] = selected_volume
        
        if not selected_volume:
            return None

        # Step 2: Get the list of all issues for the volume (cached per folder)
        issues_map = volume_issues_cache.get(folder_path)
        if issues_map is None:
            issues_map = fetch_volume_issues(selected_volume)
            volume_issues_cache[folder_path] = issues_map
        
        if not issues_map:
            return None

        # Step 3: Find the specific issue in the cached list
        issue_summary = issues_map.get(str(int(issue_number))) # Normalize issue number
        if not issue_summary:
            print(f"  Could not find issue #{issue_number} in the series list.")
            return None
            
        # Step 4: Fetch the detailed metadata for the specific issue
        return fetch_issue_details(issue_summary, selected_volume)

    else:
        print("  Could not guess series and issue from filename.")
        return None

def fetch_volume_issues(volume):
    """
    Fetches all issues for a given volume and returns a map of issue numbers to issue summaries.
    """
    volume_name = volume.get('name')
    volume_id = volume.get('id')
    print(f"  Fetching all issues for volume '{volume_name}' (ID: {volume_id})...")

    volume_url = f"https://comicvine.gamespot.com/api/volume/4050-{volume_id}/"
    params = { "api_key": COMICVINE_API_KEY, "format": "json", "field_list": "issues" }
    headers = { "User-Agent": "ComicOrganizer/1.0" }

    try:
        response = requests.get(volume_url, params=params, headers=headers)
        response.raise_for_status()
        issues = response.json().get('results', {}).get('issues', [])
        
        # Create a map of issue number to issue summary for quick lookups
        issues_map = {issue['issue_number']: issue for issue in issues}
        print(f"  Found and cached {len(issues_map)} issues for this volume.")
        return issues_map

    except requests.exceptions.RequestException as e:
        print(f"  Error fetching volume issues: {e}")
        return {}

def fetch_issue_details(issue_summary, volume):
    """
    Fetches the full details for a single issue.
    """
    issue_id = issue_summary.get('id')
    print(f"  Fetching details for issue ID: {issue_id}...")

    issue_url = f"https://comicvine.gamespot.com/api/issue/4000-{issue_id}/"
    params = {
        "api_key": COMICVINE_API_KEY,
        "format": "json",
        "field_list": "name,issue_number,description,cover_date,volume,person_credits,character_credits,team_credits,location_credits,site_detail_url"
    }
    headers = { "User-Agent": "ComicOrganizer/1.0" }

    try:
        response = requests.get(issue_url, params=params, headers=headers)
        response.raise_for_status()
        issue_details = response.json().get('results')
        if issue_details:
            issue_details['volume'] = volume  # Inject the full volume info
            print(f"  Found issue: {issue_details.get('name') or volume.get('name')} ({issue_details.get('id')})")
            return issue_details
        return None
    except requests.exceptions.RequestException as e:
        print(f"  Error fetching issue details: {e}")
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



def generate_comic_info_xml(issue_details):
    """
    Generates the ComicInfo.xml content as a string.
    """
    if not issue_details:
        return None

    volume_info = issue_details.get('volume', {})
    
    # Create the root element
    root = ET.Element('ComicInfo', {
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'xmlns:xsd': 'http://www.w3.org/2001/XMLSchema'
    })

    # Helper to add a sub-element if the value exists
    def add_element(parent, tag, value):
        if value:
            el = ET.SubElement(parent, tag)
            el.text = str(value)

    add_element(root, 'Title', issue_details.get('name'))
    add_element(root, 'Series', volume_info.get('name'))
    add_element(root, 'Number', issue_details.get('issue_number'))
    add_element(root, 'Volume', volume_info.get('start_year'))
    add_element(root, 'Publisher', volume_info.get('publisher', {}).get('name'))
    add_element(root, 'Web', issue_details.get('site_detail_url'))

    # Add summary, handling potential HTML
    summary = issue_details.get('description')
    if summary:
        summary_el = ET.SubElement(root, 'Summary')
        summary_el.text = summary  # The XML library handles escaping

    # Add date fields
    cover_date_str = issue_details.get('cover_date')
    if cover_date_str:
        try:
            cover_date = datetime.strptime(cover_date_str, '%Y-%m-%d')
            add_element(root, 'Year', cover_date.year)
            add_element(root, 'Month', cover_date.month)
            add_element(root, 'Day', cover_date.day)
        except (ValueError, TypeError):
            pass

    # Add credits
    add_element(root, 'Writer', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'writer' in p['role'].lower()]))
    add_element(root, 'Penciller', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'penciller' in p['role'].lower()]))
    add_element(root, 'Inker', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'inker' in p['role'].lower()]))
    add_element(root, 'Colorist', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'colorist' in p['role'].lower()]))
    add_element(root, 'Letterer', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'letterer' in p['role'].lower()]))
    add_element(root, 'CoverArtist', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'cover' in p['role'].lower()]))
    add_element(root, 'Editor', ', '.join([p['name'] for p in issue_details.get('person_credits', []) if 'editor' in p['role'].lower()]))
    
    add_element(root, 'Characters', ', '.join([c['name'] for c in issue_details.get('character_credits', [])]))
    add_element(root, 'Teams', ', '.join([t['name'] for t in issue_details.get('team_credits', [])]))
    add_element(root, 'Locations', ', '.join([l['name'] for l in issue_details.get('location_credits', [])]))

    # Convert the XML tree to a string
    return ET.tostring(root, encoding='unicode')


def organize_file(original_path, issue_details, output_dir, dry_run=False):
    if not issue_details:
        return

    volume_info = issue_details.get('volume', {})
    series_name_raw = volume_info.get('name')
    
    # Sanitize the series name for use in file paths
    series_name = sanitize_filename(series_name_raw)

    volume_year = volume_info.get('start_year')
    issue_number_str = issue_details.get('issue_number')
    
    if not all([series_name, volume_year, issue_number_str]):
        print("  Could not determine new file name. Missing required details.")
        return

    # Format the issue number to be three digits with leading zeros
    issue_number_padded = issue_number_str.zfill(3)

    # Format the cover date
    cover_date_str = issue_details.get('cover_date')
    if cover_date_str:
        try:
            cover_date = datetime.strptime(cover_date_str, '%Y-%m-%d')
            date_formatted = cover_date.strftime('%B %Y')
        except (ValueError, TypeError):
            date_formatted = "Unknown Date"
    else:
        date_formatted = "Unknown Date"

    # Check for "Annual" in the original filename
    annual_str = " Annual" if "annual" in os.path.basename(original_path).lower() else ""

    # Construct the new filename
    _, extension = os.path.splitext(original_path)
    new_file_name = f"{series_name} V{volume_year}{annual_str} #{issue_number_padded} ({date_formatted}){extension}"
    
    # Construct the new folder path
    new_series_folder = os.path.join(output_dir, f"{series_name} ({volume_year})")
    new_file_path = os.path.join(new_series_folder, new_file_name)

    # Generate ComicInfo.xml
    comic_info_xml = generate_comic_info_xml(issue_details)

    if dry_run:
        print(f"  [DRY RUN] Would move and rename to: {new_file_path}")
        if comic_info_xml:
            print("  [DRY RUN] Would generate and embed ComicInfo.xml.")
    else:
        print(f"  Moving and renaming to: {new_file_path}")
        os.makedirs(new_series_folder, exist_ok=True)
        
        # Before moving, check if the destination is the same as the source
        if original_path != new_file_path:
            os.rename(original_path, new_file_path)
        else:
            print("  Skipping move, file already organized.")

        if comic_info_xml:
            if new_file_path.lower().endswith('.cbz'):
                try:
                    with zipfile.ZipFile(new_file_path, 'a') as zf:
                        # Check if ComicInfo.xml already exists
                        if 'ComicInfo.xml' not in zf.namelist():
                            zf.writestr('ComicInfo.xml', comic_info_xml)
                            print("  Successfully embedded ComicInfo.xml.")
                        else:
                            print("  ComicInfo.xml already exists. Skipping embedding.")
                except Exception as e:
                    print(f"  Error embedding ComicInfo.xml: {e}")
            elif new_file_path.lower().endswith('.cbr'):
                print("  Skipping ComicInfo.xml embedding for .cbr file (modification not yet supported).")

def sanitize_filename(name):
    """Removes characters that are invalid for file and directory names."""
    if not name:
        return ""
    # Replace slashes and colons with a hyphen
    name = name.replace('/', '-').replace(':', '-')
    # Remove other invalid characters
    invalid_chars = r'<>:"\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    return name


def convert_cbr_to_cbz(cbr_path):
    """
    Converts a .cbr file to a .cbz file.
    """
    cbz_path = os.path.splitext(cbr_path)[0] + '.cbz'
    temp_dir = tempfile.mkdtemp()
    
    try:
        print(f"Converting {cbr_path} to .cbz...")
        with RarFile(cbr_path, 'r') as archive:
            archive.extractall(temp_dir)
        
        with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(temp_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_dir)
                    zf.write(file_path, arcname)
        
        # Validate the new cbz file
        with zipfile.ZipFile(cbz_path, 'r') as zf:
            if zf.testzip() is not None:
                raise Exception("Failed to validate the new .cbz file.")
        
        print(f"  Successfully converted to {cbz_path}")
        os.remove(cbr_path)
        return cbz_path

    except Exception as e:
        print(f"  Error converting {cbr_path}: {e}")
        # Clean up the partially created .cbz file if conversion fails
        if os.path.exists(cbz_path):
            os.remove(cbz_path)
        return None
    finally:
        shutil.rmtree(temp_dir)



def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Organize comic book files.')
    parser.add_argument('input_dir', help='The directory containing the unorganized comic files.')
    parser.add_argument('output_dir', help='The directory to store the organized comic files.')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without moving files.')
    args = parser.parse_args()

    global COMICVINE_API_KEY
    COMICVINE_API_KEY = os.getenv("COMICVINE_API_KEY")

    # Convert all .cbr files to .cbz before processing
    if not args.dry_run:
        cbr_files = [f for f in scan_comic_files(args.input_dir) if f.lower().endswith('.cbr')]
        for cbr_file in cbr_files:
            convert_cbr_to_cbz(cbr_file)

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
    volume_issues_cache = {}

    for folder, comics in comics_by_folder.items():
        print(f"\nProcessing folder: {folder}")
        for comic_file in comics:
            print(f"Processing {comic_file}...")
            cover_image = extract_cover_image(comic_file)
            if cover_image:
                print(f"  Successfully extracted cover image.")
                issue_details = identify_comic(comic_file, cover_image, series_cache, volume_issues_cache)
                organize_file(comic_file, issue_details, args.output_dir, args.dry_run)
            else:
                print(f"  Could not extract cover image.")


if __name__ == '__main__':
    main()