import argparse
from colorama import Fore, Style, init
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
import json
import time
from pathlib import Path
from functools import wraps

# Rate limiting for Comic Vine API
COMICVINE_API_KEY = ""
LAST_API_CALL_TIME = 0
API_CALL_TIMESTAMPS = []
HOURLY_LIMIT = 199  # Leave a small buffer
MIN_SECONDS_BETWEEN_CALLS = 4.0

def rate_limited():
    """
    Decorator to ensure API calls respect both a minimum delay and an hourly limit.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            global LAST_API_CALL_TIME, API_CALL_TIMESTAMPS

            # 1. Enforce minimum time between calls
            current_time = time.time()
            time_since_last_call = current_time - LAST_API_CALL_TIME
            if time_since_last_call < MIN_SECONDS_BETWEEN_CALLS:
                sleep_time = MIN_SECONDS_BETWEEN_CALLS - time_since_last_call
                print(f"{Style.DIM} Rate limit: sleeping for {sleep_time:.2f}s to maintain call frequency.{Style.RESET_ALL}")
                time.sleep(sleep_time)

            # 2. Enforce hourly limit
            one_hour_ago = time.time() - 3600
            # Remove timestamps older than an hour
            API_CALL_TIMESTAMPS[:] = [t for t in API_CALL_TIMESTAMPS if t > one_hour_ago]

            if len(API_CALL_TIMESTAMPS) >= HOURLY_LIMIT:
                oldest_call = API_CALL_TIMESTAMPS[0]
                wait_time = (oldest_call + 3600) - time.time()
                if wait_time > 0:
                    print(f"{Fore.YELLOW} ⚠️ Rate limit: hourly limit reached. Waiting for {wait_time:.2f}s.{Style.RESET_ALL}")
                    time.sleep(wait_time)

            # Make the API call
            result = func(*args, **kwargs)

            # Record the call
            LAST_API_CALL_TIME = time.time()
            API_CALL_TIMESTAMPS.append(LAST_API_CALL_TIME)

            return result
        return wrapper
    return decorator

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
        print(f"{Fore.RED} ✗ Error extracting cover from {comic_file_path}: {e}{Style.RESET_ALL}")
    return None

import re

def identify_comic(comic_file_path, cover_image, series_cache, volume_issues_cache, issue_details_cache, output_dir, dry_run):
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
    
    # Pre-process the filename to remove content in parentheses
    clean_file_name = re.sub(r'\(.*?\)', '', file_name)

    # 1. Prioritize numbers prefixed with '#'
    hash_match = re.search(r'#(\d+)', clean_file_name)
    if hash_match:
        issue_number = hash_match.group(1)
    else:
        # 2. Find all standalone numbers in the filename
        potential_numbers = re.findall(r'\b\d+\b', clean_file_name)
        
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
        print(f"{Fore.CYAN} 🏃‍➡️ Guessing info from filename: {file_name}{Style.RESET_ALL}")
        issue_number = guess.get('issue') or guess.get('episode')

    if cover_image:
        cover_hash = imagehash.phash(cover_image)
        print(f"{Fore.GREEN}✔ Cover hash: {cover_hash}{Style.RESET_ALL}")

    if series_title and issue_number:
        print(f"{Fore.CYAN} 🏃‍➡️ Guessed Series: {series_title}, Issue: {issue_number}{Style.RESET_ALL}")
        
        # Step 1: Get the selected volume (cached per folder)
        selected_volume = series_cache.get(folder_path)
        if selected_volume is None:
            volume_summary = select_series(series_title, series_year)
            if not volume_summary:
                series_cache[folder_path] = None  # Cache failure
                return None
            
            selected_volume = handle_series_selection(volume_summary, output_dir, dry_run)
            series_cache[folder_path] = selected_volume  # Cache the detailed volume
        
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
            print(f"{Fore.YELLOW} ⚠️ Issue #{issue_number} not found in the fetched issue list.{Style.RESET_ALL}")
            return None

        # Step 4: Check cache or fetch the detailed metadata for the specific issue
        volume_id = selected_volume.get('id')
        issue_num_str = issue_summary.get('issue_number')
        cache_key = f"{volume_id}-{issue_num_str}"

        if cache_key in issue_details_cache:
            print(f"{Fore.GREEN}✔ Found issue #{issue_num_str} in cache. Skipping API call.{Style.RESET_ALL}")
            return issue_details_cache[cache_key]
        else:
            issue_details = fetch_issue_details(issue_summary, selected_volume)
            if issue_details:
                print(f"{Fore.GREEN}✔ Adding issue #{issue_num_str} to cache.{Style.RESET_ALL}")
                issue_details_cache[cache_key] = issue_details
            return issue_details

    else:
        print(f"{Fore.YELLOW} ⚠️ Could not guess issue number from '{file_name}'. Skipping.{Style.RESET_ALL}")
        return None

def handle_series_selection(volume_summary, output_dir, dry_run):
    """
    Handles logic for creating or loading a series.json file after a series is selected.
    """
    series_name = sanitize_filename(volume_summary.get('name'))
    volume_year = volume_summary.get('start_year')
    new_series_folder = os.path.join(output_dir, f"{series_name} ({volume_year})")
    series_json_path = os.path.join(new_series_folder, 'series.json')

    if os.path.exists(series_json_path):
        print(f"{Fore.GREEN}✔ Found existing series.json at: {series_json_path}{Style.RESET_ALL}")
        try:
            with open(series_json_path, 'r', encoding='utf-8') as f:
                series_data = json.load(f)
            
            metadata = series_data.get('metadata', {})
            return {
                'id': metadata.get('comicid'),
                'name': metadata.get('name'),
                'start_year': str(metadata.get('year')),
                'publisher': {'name': metadata.get('publisher')},
                'description': metadata.get('description_formatted'),
                'count_of_issues': metadata.get('total_issues'),
                'image': {'original_url': metadata.get('comic_image')}
            }
        except (json.JSONDecodeError, KeyError) as e:
            print(f"{Fore.RED} ✗ Warning: Could not read existing series.json ({e}). Will fetch from API.{Style.RESET_ALL}")

    print(f"{Fore.CYAN}🏃‍➡️ No series.json found. Fetching details from Comic Vine...{Style.RESET_ALL}")
    volume_id = volume_summary.get('id')
    series_details = fetch_series_details(volume_id)
    if not series_details:
        print(f"{Fore.RED} ✗ Failed to fetch issue details.{Style.RESET_ALL}")
        return None

    generate_and_write_series_json(series_details, new_series_folder, dry_run)
    return series_details


@rate_limited()
def fetch_series_details(volume_id):
    """Fetches comprehensive details for a given volume."""
    print(f"{Fore.CYAN} 🏃‍➡️ Fetching full details for volume ID: {volume_id}...{Style.RESET_ALL}")
    url = f"https://comicvine.gamespot.com/api/volume/4050-{volume_id}/"
    params = {
        "api_key": COMICVINE_API_KEY,
        "format": "json",
        "field_list": "id,name,start_year,publisher,description,count_of_issues,image,last_issue,first_issue"
    }
    headers = {"User-Agent": "ComicOrganizer/1.0"}
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json().get('results')
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED} ✗ Error fetching series details: {e}{Style.RESET_ALL}")
        return None

def generate_and_write_series_json(series_details, series_folder, dry_run):
    """Generates and writes the series.json file."""
    last_issue = series_details.get('last_issue')
    series_status = 'Ended'
    if last_issue and last_issue.get('cover_date'):
        try:
            last_date = datetime.strptime(last_issue['cover_date'], '%Y-%m-%d').date()
            if (datetime.now().date() - last_date).days < 90:
                series_status = 'Continuing'
        except (ValueError, TypeError):
            pass
    elif series_details.get('count_of_issues', 0) == 0:
        series_status = 'Continuing'

    pub_run = series_details.get('start_year', '')
    if last_issue and last_issue.get('cover_date'):
        try:
            last_year = datetime.strptime(last_issue['cover_date'], '%Y-%m-%d').year
            if str(last_year) != pub_run:
                pub_run += f" - {last_year}"
        except (ValueError, TypeError):
            pass

    booktype = 'Standard'
    if series_details.get('count_of_issues') == 1:
        booktype = 'One-Shot'

    description = series_details.get('description', '') or ''
    
    metadata = {
        'version': '1.0.2',
        'metadata': {
            'type': 'comicSeries',
            'publisher': series_details.get('publisher', {}).get('name'),
            'imprint': series_details.get('publisher', {}).get('name'),
            'name': series_details.get('name'),
            'comicid': series_details.get('id'),
            'year': int(series_details['start_year']) if series_details.get('start_year') else None,
            'description_text': re.sub(r'<[^>]+>', '', description),
            'description_formatted': description,
            'volume': None,
            'booktype': booktype,
            'age_rating': None,
            'collects': None,
            'comic_image': series_details.get('image', {}).get('original_url'),
            'total_issues': series_details.get('count_of_issues'),
            'publication_run': pub_run,
            'status': series_status
        }
    }

    if not dry_run:
        try:
            os.makedirs(series_folder, exist_ok=True)
            series_json_path = os.path.join(series_folder, 'series.json')
            with open(series_json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=4, ensure_ascii=False)
            print(f"{Fore.GREEN}✔ Successfully wrote series.json to: {series_folder}{Style.RESET_ALL}")
        except IOError as e:
            print(f"{Fore.RED} ✗ Error writing series.json: {e}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would write series.json to: {series_folder}{Style.RESET_ALL}")

@rate_limited()
def fetch_volume_issues(volume):
    """
    Fetches all issues for a given volume and returns a map of issue numbers to issue summaries.
    Rate limited to 1 request per X seconds.
    """
    volume_name = volume.get('name')
    volume_id = volume.get('id')
    print(f"{Fore.CYAN} 🏃‍➡️ Fetching all issues for volume '{volume_name}' (ID: {volume_id})...{Style.RESET_ALL}")

    volume_url = f"https://comicvine.gamespot.com/api/volume/4050-{volume_id}/"
    params = { "api_key": COMICVINE_API_KEY, "format": "json", "field_list": "issues" }
    headers = { "User-Agent": "ComicOrganizer/1.0" }

    try:
        response = requests.get(volume_url, params=params, headers=headers)
        response.raise_for_status()
        issues = response.json().get('results', {}).get('issues', [])
        
        # Create a map of issue number to issue summary for quick lookups
        issues_map = {issue['issue_number']: issue for issue in issues}
        print(f"{Fore.GREEN}✔ Found and cached {len(issues_map)} issues for this volume.{Style.RESET_ALL}")
        return issues_map

    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED} ✗ Error fetching volume issues: {e}{Style.RESET_ALL}")
        return {}

@rate_limited()
def fetch_issue_details(issue_summary, volume):
    """
    Fetches the full details for a single issue.
    Rate limited to 1 request per X seconds.
    """
    issue_id = issue_summary.get('id')
    print(f"{Fore.CYAN} 🏃‍➡️ Fetching details for issue ID: {issue_id}...{Style.RESET_ALL}")

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
            print(f"{Fore.GREEN}✔ Found issue: {issue_details.get('name') or volume.get('name')} ({issue_details.get('id')}){Style.RESET_ALL}")
            return issue_details
        return None
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED} ✗ Error fetching issue details: {e}{Style.RESET_ALL}")
        return None


@rate_limited()
def select_series(series_title, series_year=None):
    """
    Searches for a series and prompts the user to select from the results.
    Rate limited to 1 request per X seconds.
    """
    if not COMICVINE_API_KEY:
        print("  Comic Vine API key is not set. Skipping search.")
        return None

    print(f"{Fore.CYAN} 🏃‍➡️ Searching Comic Vine for series '{series_title}' (Year: {series_year or 'Any'})...{Style.RESET_ALL}")

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
            print(f"{Fore.CYAN} 🏃‍➡️ No series found in cache for '{series_title}'. Searching ComicVine.{Style.RESET_ALL}")
            return None

        volume = None
        if len(results) > 1:
            print(f"{Fore.YELLOW} 👉 Multiple series found. Please select one:{Style.RESET_ALL}")
            for i, res in enumerate(results):
                print(f"    {Fore.CYAN}{i+1}:{Style.RESET_ALL} {res.get('name')} ({res.get('start_year')}) - {Style.DIM}{res.get('site_detail_url')}{Style.RESET_ALL}")
            print(f"    {Fore.CYAN}{len(results)+1}:{Style.RESET_ALL} None of the above")

            while True:
                try:
                    choice = int(input(f"{Fore.YELLOW} 👉 Enter your choice: {Style.RESET_ALL}"))
                    if 1 <= choice <= len(results):
                        volume = results[choice-1]
                        break
                    elif choice == len(results) + 1:
                        return None
                    else:
                        print(f"{Fore.RED} ✗ Invalid choice. Please try again.{Style.RESET_ALL}")
                except ValueError:
                    print(f"{Fore.RED} ✗ Invalid input. Please enter a number.{Style.RESET_ALL}")
        elif results:
            volume = results[0]
        
        return volume

    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED} ✗ Error searching Comic Vine: {e}{Style.RESET_ALL}")
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
        return None

    volume_info = issue_details.get('volume', {})
    series_name_raw = volume_info.get('name')
    
    # Sanitize the series name for use in file paths
    series_name = sanitize_filename(series_name_raw)

    volume_year = volume_info.get('start_year')
    issue_number_str = issue_details.get('issue_number')
    
    if not all([series_name, volume_year, issue_number_str]):
        print(f"{Fore.RED} ✗ Could not determine new file name. Missing required details.{Style.RESET_ALL}")
        return None

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
        print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would move and rename to: {new_file_path}{Style.RESET_ALL}")
        if comic_info_xml:
            print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would generate and embed ComicInfo.xml.{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN} 📦 Moving and renaming to: {new_file_path}{Style.RESET_ALL}")
        os.makedirs(new_series_folder, exist_ok=True)
        
        # Before moving, check if the destination is the same as the source
        if original_path != new_file_path:
            shutil.move(original_path, new_file_path)
        else:
            print(f"{Fore.YELLOW} ⚠️ Skipping move, file already organized.{Style.RESET_ALL}")

        if comic_info_xml:
            if new_file_path.lower().endswith('.cbz'):
                try:
                    with zipfile.ZipFile(new_file_path, 'a') as zf:
                        # Check if ComicInfo.xml already exists
                        if 'ComicInfo.xml' not in zf.namelist():
                            zf.writestr('ComicInfo.xml', comic_info_xml)
                            print(f"{Fore.GREEN} ✔ Successfully embedded ComicInfo.xml.{Style.RESET_ALL}")
                        else:
                            print(f"{Fore.YELLOW} ⚠️ ComicInfo.xml already exists. Skipping embedding.{Style.RESET_ALL}")
                except Exception as e:
                    print(f"{Fore.RED} ✗ Error embedding ComicInfo.xml: {e}{Style.RESET_ALL}")
            elif new_file_path.lower().endswith('.cbr'):
                print(f"{Fore.YELLOW} ⚠️ Skipping ComicInfo.xml embedding for .cbr file (modification not yet supported).{Style.RESET_ALL}")
    
    return new_file_path

def sanitize_filename(name):
    """Removes characters that are invalid for file and directory names."""
    if not name:
        return ""
    # Replace slashes with hyphens and colons with space-hyphen-space
    name = name.replace('/', ' - ').replace(':', ' - ')
    # Remove other invalid characters
    invalid_chars = r'<>:"\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    # Clean up any double spaces that might have been created
    while '  ' in name:
        name = name.replace('  ', ' ')
    return name.strip()


def convert_cbr_to_cbz(cbr_path):
    """
    Converts a .cbr file to a .cbz file.
    """
    cbz_path = os.path.splitext(cbr_path)[0] + '.cbz'
    temp_dir = tempfile.mkdtemp()
    
    try:
        print(f"{Fore.CYAN} 🔄 Converting {cbr_path} to .cbz...{Style.RESET_ALL}")
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
        
        print(f"{Fore.GREEN} ✔ Successfully converted to {cbz_path}{Style.RESET_ALL}")
        os.remove(cbr_path)
        return cbz_path

    except Exception as e:
        print(f"{Fore.RED} ✗ Error converting {cbr_path}: {e}{Style.RESET_ALL}")
        # Clean up the partially created .cbz file if conversion fails
        if os.path.exists(cbz_path):
            os.remove(cbz_path)
        return None
    finally:
        shutil.rmtree(temp_dir)



def main():
    # Parse command line arguments first
    parser = argparse.ArgumentParser(description='Organize comic book files.')
    parser.add_argument('input_dir', nargs='?', default=None, help='The directory containing the comic files to organize. Defaults to the current directory if not specified.')
    parser.add_argument('output_dir', nargs='?', default=None, help='(Optional) The directory to store the organized files. If not provided, organizes in-place.')
    parser.add_argument('--series-folder', help='(Optional) The name of a specific series folder to process within the input directory.')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without moving files.')
    parser.add_argument('--force-refresh', action='store_true', help='Force a refresh of cached data for a specific series folder.')
    parser.add_argument('-y', '--yes', action='store_true', help='Automatically answer yes to all prompts and skip confirmations.')
    parser.add_argument('--comicvine-api-key', help='Set or update your Comic Vine API key. This will be saved for future use.')
    init(autoreset=True)
    args = parser.parse_args()

    print(f"""
{Fore.RED} ______     {Fore.YELLOW}__  __     {Fore.GREEN}__   __     {Fore.CYAN}______     {Fore.BLUE}______     {Fore.MAGENTA}______    
{Fore.RED}/\  == \   {Fore.YELLOW}/\ \/\ \   {Fore.GREEN}/\ \"-.\ \   {Fore.CYAN}/\  __ \   {Fore.BLUE}/\  == \   {Fore.MAGENTA}/\  == \   
{Fore.RED}\\ \  __<   {Fore.YELLOW}\ \ \_\ \  {Fore.GREEN}\ \ \-.  \  {Fore.CYAN}\ \  __ \  {Fore.BLUE}\ \  __<   {Fore.MAGENTA}\ \  __<   
{Fore.RED} \\ \_\ \_\  {Fore.YELLOW}\ \\_____\  {Fore.GREEN}\ \_\\" \_\  {Fore.CYAN}\ \_\ \_\  {Fore.BLUE}\ \_\ \_\  {Fore.MAGENTA}\ \_\ \_\ 
{Fore.RED}  \/_/ /_/   {Fore.YELLOW}\/_____/   {Fore.GREEN}\/_/ \/_/   {Fore.CYAN}\/_/\/_/   {Fore.BLUE}\/_/ /_/   {Fore.MAGENTA}\/_/ /_/ 
              @luccasveg 2025 🏃‍➡️ https://github.com/luccast/Runarr
{Style.RESET_ALL}                                                                    
""")
    
    # Set up config directory and file
    config_dir = Path.home() / '.runarr'
    config_file = config_dir / 'config.json'
    cache_file = config_dir / 'cache.json'
    
    # Create config directory if it doesn't exist
    config_dir.mkdir(exist_ok=True, mode=0o700)  # Create with secure permissions
    
    # Load existing config if it exists
    config = {}
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print(f"{Fore.YELLOW} ⚠️ Warning: Config file is corrupted. Creating a new one.{Style.RESET_ALL}")

    # Load issue details cache
    issue_details_cache = {}
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                issue_details_cache = json.load(f)
            print(f"{Fore.CYAN} 🏃‍➡️ Loaded {len(issue_details_cache)} items from cache.{Style.RESET_ALL}")
        except json.JSONDecodeError:
            print(f"{Fore.YELLOW} ⚠️ Warning: Cache file is corrupted. Starting with an empty cache.{Style.RESET_ALL}")
    
    # Get API key from command line, environment, or config
    global COMICVINE_API_KEY
    if args.comicvine_api_key:
        # Save the API key if provided via command line
        config['comicvine_api_key'] = args.comicvine_api_key
        with open(config_file, 'w') as f:
            json.dump(config, f)
        print(f"{Fore.GREEN} ✔ Comic Vine API key has been saved.{Style.RESET_ALL}")
        COMICVINE_API_KEY = args.comicvine_api_key
    else:
        # Try to get the API key from config or environment
        COMICVINE_API_KEY = config.get('comicvine_api_key') or os.getenv('COMICVINE_API_KEY')
    
    if not COMICVINE_API_KEY:
        print(f"""{Fore.RED} ✗ Error: No Comic Vine API key found.
{Style.RESET_ALL}Please provide your API key using one of these methods:
1. Run with {Style.BRIGHT}--comicvine-api-key "your_api_key_here"{Style.RESET_ALL}
2. Set the {Style.BRIGHT}COMICVINE_API_KEY{Style.RESET_ALL} environment variable

Get an API key from: {Fore.BLUE}https://comicvine.gamespot.com/api/{Style.RESET_ALL}
""")
        return

    # Determine input_dir
    input_dir = args.input_dir if args.input_dir else os.getcwd()
    if args.input_dir is None:
        print(f"{Style.DIM}No input directory specified. Using current directory: {input_dir}{Style.RESET_ALL}")

    # API key is now loaded at the beginning of main()

    # Determine the base output directory
    base_output_dir = args.output_dir if args.output_dir else input_dir

    # Handle the --series-folder argument
    if args.series_folder:
        target_folder = os.path.join(input_dir, args.series_folder)
        if not os.path.isdir(target_folder):
            print(f"{Fore.RED} ✗ Error: The specified series folder does not exist: {target_folder}{Style.RESET_ALL}")
            return
        comics_by_folder = {target_folder: [os.path.join(target_folder, f) for f in os.listdir(target_folder) if f.lower().endswith(('.cbz', '.cbr'))]}
    else:
        # Group all comics by their parent directory
        all_comic_files = scan_comic_files(input_dir)
        comics_by_folder = {}
        for comic_file in all_comic_files:
            folder = os.path.dirname(comic_file)
            if folder not in comics_by_folder:
                comics_by_folder[folder] = []
            comics_by_folder[folder].append(comic_file)

    series_cache = {}
    volume_issues_cache = {}

    try:
        for folder, comics in comics_by_folder.items():
            print(f"\n{Style.BRIGHT}{Fore.MAGENTA} 🗂️ Processing folder: {folder}{Style.RESET_ALL}")

            # Confirm with the user before processing
            if not args.yes:
                confirm = input(f"{Fore.YELLOW} 👉 Do you want to process this folder? (y/n): {Style.RESET_ALL}").lower().strip()
                if confirm not in ['y', 'yes']:
                    print(f"{Fore.CYAN} 🏃‍➡️ Skipping folder: {folder}{Style.RESET_ALL}")
                    continue
            
            new_series_folder_path = None
            processed_comics = set()

            # Convert .cbr to .cbz in the current folder before processing
            if not args.dry_run:
                cbr_files = [f for f in comics if f.lower().endswith('.cbr')]
                for cbr_file in cbr_files:
                    convert_cbr_to_cbz(cbr_file)
                # Refresh the file list after conversion
                comics = [f.replace('.cbr', '.cbz') if f.lower().endswith('.cbr') else f for f in comics]

            # Identify comic files and extra files
            all_files_in_folder = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
            comic_files_in_folder = [f for f in all_files_in_folder if f.lower().endswith('.cbz')]  # Only look for .cbz files now
            extra_files = [f for f in all_files_in_folder if f not in comic_files_in_folder and not f.lower().endswith('.cbr')]

            for comic_file in comic_files_in_folder:
                print(f"  {Fore.CYAN}Processing {os.path.basename(comic_file)}...{Style.RESET_ALL}")
                cover_image = extract_cover_image(comic_file)
                if cover_image:
                    print(f"  {Fore.GREEN} ✔ Successfully extracted cover image.{Style.RESET_ALL}")
                    issue_details = identify_comic(comic_file, cover_image, series_cache, volume_issues_cache, issue_details_cache, base_output_dir, args.dry_run)
                    
                    if issue_details:
                        # The organize_file function now returns the path to the *newly created* file
                        new_file_path = organize_file(comic_file, issue_details, base_output_dir, args.dry_run)
                        if new_file_path:
                            processed_comics.add(new_file_path)
                            # Determine the new series folder from the first successfully processed comic
                            if not new_series_folder_path:
                                new_series_folder_path = os.path.dirname(new_file_path)
                else:
                    print(f"  {Fore.RED} ✗ Could not extract cover image.{Style.RESET_ALL}")

            # --- Extras and Cleanup Logic ---
            if not args.dry_run and new_series_folder_path:
                # Move any remaining files to an "Extras" folder
                if extra_files:
                    extras_folder = os.path.join(new_series_folder_path, 'Extras')
                    print(f"  {Fore.CYAN} 📦 Moving {len(extra_files)} extra file(s) to: {extras_folder}{Style.RESET_ALL}")
                    os.makedirs(extras_folder, exist_ok=True)
                    for file_path in extra_files:
                        shutil.move(file_path, os.path.join(extras_folder, os.path.basename(file_path)))

                # Remove the original folder if it's empty and not the same as the new one
                if not os.listdir(folder) and folder != new_series_folder_path:
                    print(f"  {Fore.CYAN} 🗑️ Removing empty original folder: {folder}{Style.RESET_ALL}")
                    os.rmdir(folder)
    finally:
        # Save the updated cache to the file
        with open(cache_file, 'w') as f:
            json.dump(issue_details_cache, f, indent=2)
        print(f"\n{Fore.GREEN} ✔ Cache saved with {len(issue_details_cache)} items.{Style.RESET_ALL}")


if __name__ == '__main__':
    main()