import argparse
from colorama import Fore, Style, init
import os
from dotenv import load_dotenv
import guessit
import imagehash
import requests
import zipfile
from rarfile import RarFile, is_rarfile
from PIL import Image
from datetime import datetime
import xml.etree.ElementTree as ET
import tempfile
import shutil
import json
import time
from pathlib import Path
from functools import wraps
import select
import sys
from comic_organizer.comic_info import generate_comic_info_xml
from comic_organizer.series_info import generate_series_data, write_series_json

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

def interruptible_wait(duration):
    """
    Waits for a given duration, displaying a countdown.
    Can be interrupted by the user pressing 'r'.
    Returns a tuple: (was_interrupted, time_waited).
    """
    start_time = time.time()
    end_time = start_time + duration

    is_windows = sys.platform == "win32"
    if is_windows:
        import msvcrt

    prompt = f"(Press 'r' to retry now)" if is_windows else f"(Press 'r' then Enter to retry now)"

    while time.time() < end_time:
        remaining = int(end_time - time.time())
        sys.stdout.write(f"\r{Fore.YELLOW} ⏳ Waiting for {remaining // 60:02d}m {remaining % 60:02d}s... {prompt} {Style.RESET_ALL}")
        sys.stdout.flush()

        interrupted = False
        if is_windows:
            # On Windows, check for key press for 1 second
            wait_start = time.time()
            while time.time() - wait_start < 1.0:
                if msvcrt.kbhit() and msvcrt.getch().decode('utf-8').lower() == 'r':
                    interrupted = True
                    break
                time.sleep(0.1) # Avoid busy-waiting
        else:
            # On POSIX, wait for input for 1 second
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist and sys.stdin.readline().strip().lower() == 'r':
                interrupted = True

        if interrupted:
            sys.stdout.write("\r" + " " * 80 + "\r") # Clear line
            return True, time.time() - start_time

    # Wait finished without interruption
    sys.stdout.write("\r" + " " * 80 + "\r") # Clear line
    return False, duration



def make_api_request(url, params, headers):
    """
    Makes an API request with retry logic for 420 errors, including an interruptible wait.
    Returns the response object on success, None on failure.
    """
    try:
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response is not None and e.response.status_code == 420:
            wait_duration = 3600
            while wait_duration > 0:
                print(f"{Fore.YELLOW} ⚠️ API rate limit (420) hit. Waiting...{Style.RESET_ALL}")
                
                retry_now, time_waited = interruptible_wait(wait_duration)
                wait_duration -= time_waited

                if retry_now or wait_duration <= 0:
                    print(f"\n{Fore.CYAN} 🏃‍➡️ Retrying request to {url}...{Style.RESET_ALL}")
                    try:
                        response = requests.get(url, params=params, headers=headers)
                        response.raise_for_status()
                        return response  # Success!
                    except requests.exceptions.RequestException as retry_e:
                        if hasattr(retry_e, 'response') and retry_e.response is not None and retry_e.response.status_code == 420:
                            print(f"{Fore.RED} ✗ Retry failed. Resuming wait.{Style.RESET_ALL}")
                            continue  # Continue the while loop to wait more
                        else:
                            print(f"{Fore.RED} ✗ Error on retry: {retry_e}{Style.RESET_ALL}")
                            return None # Different error, give up
            
            print(f"{Fore.RED} ✗ Could not complete request after waiting and retrying.{Style.RESET_ALL}")
            return None
        else:
            print(f"{Fore.RED} ✗ API Request Error: {e}{Style.RESET_ALL}")
            return None

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

def load_volume_from_series_json(folder_path, overwrite=False):
    """
    Loads volume information from a series.json file if it exists in the folder.
    """
    if overwrite:
        print(f"{Fore.YELLOW} ⚠️ Overwrite flag is set. Ignoring existing series.json.{Style.RESET_ALL}")
        return None
        
    series_json_path = os.path.join(folder_path, 'series.json')
    if os.path.exists(series_json_path):
        print(f"{Fore.GREEN}✔ Found existing series.json at: {series_json_path}{Style.RESET_ALL}")
        try:
            with open(series_json_path, 'r', encoding='utf-8') as f:
                series_data = json.load(f)
            
            metadata = series_data.get('metadata', {})
            # Reconstruct the volume summary from the series.json
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
    return None

import re

def identify_comic(comic_file_path, cover_image, series_cache, volume_issues_cache, issue_details_cache, output_dir, dry_run, version_str=None, overwrite=False):
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
            # Try to load from existing series.json first
            selected_volume = load_volume_from_series_json(folder_path, overwrite=overwrite)
            
            if not selected_volume:
                # If not found or failed to load, then go to the API
                volume_summary = select_series(series_title, series_year)
                if not volume_summary:
                    series_cache[folder_path] = None  # Cache failure
                    return None
                
                selected_volume = handle_series_selection(volume_summary, output_dir, dry_run, version_str, overwrite=overwrite)
            
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

        if cache_key in issue_details_cache and not overwrite:
            print(f"{Fore.GREEN}✔ Found issue #{issue_num_str} in cache. Skipping API call.{Style.RESET_ALL}")
            return issue_details_cache[cache_key]
        else:
            if overwrite and cache_key in issue_details_cache:
                print(f"{Fore.YELLOW} ⚠️ Overwrite flag is set. Re-fetching details for issue #{issue_num_str} from API.{Style.RESET_ALL}")
            
            issue_details = fetch_issue_details(issue_summary, selected_volume)
            if issue_details:
                print(f"{Fore.GREEN}✔ Adding issue #{issue_num_str} to cache.{Style.RESET_ALL}")
                issue_details_cache[cache_key] = issue_details
            return issue_details

    else:
        print(f"{Fore.YELLOW} ⚠️ Could not guess issue number from '{file_name}'. Skipping.{Style.RESET_ALL}")
        return None

def handle_series_selection(volume_summary, output_dir, dry_run, version_str=None, overwrite=False):
    """
    Handles logic for creating or loading a series.json file after a series is selected.
    """
    series_name = sanitize_filename(volume_summary.get('name'))
    volume_year = volume_summary.get('start_year')
    
    # Add the version string to the folder name if it exists
    folder_version_str = f" {version_str}" if version_str else ""
    new_series_folder = os.path.join(output_dir, f"{series_name}{folder_version_str} ({volume_year})")
    
    series_json_path = os.path.join(new_series_folder, 'series.json')

    if overwrite and os.path.exists(series_json_path):
        print(f"{Fore.YELLOW} ⚠️ Overwrite flag is set. Ignoring existing series.json in target folder.{Style.RESET_ALL}")

    if os.path.exists(series_json_path) and not overwrite:
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

    # Check if we already have full details (e.g., from a URL paste)
    # 'last_issue' is a field in the full details but not the search summary.
    if 'last_issue' in volume_summary:
        print(f"{Fore.CYAN}🏃‍➡️ Using pre-fetched series details...{Style.RESET_ALL}")
        series_details = volume_summary
    else:
        print(f"{Fore.CYAN}🏃‍➡️ No series.json found. Fetching details from Comic Vine...{Style.RESET_ALL}")
        volume_id = volume_summary.get('id')
        series_details = fetch_series_details(volume_id)

    if not series_details:
        print(f"{Fore.RED} ✗ Failed to fetch series details.{Style.RESET_ALL}")
        return None

    series_data = generate_series_data(series_details)
    if series_data:
        write_series_json(series_data, new_series_folder, dry_run)
    return series_details


@rate_limited()
def fetch_series_details(volume_id):
    """Fetches comprehensive details for a given volume."""
    print(f"{Fore.CYAN} 🏃‍➡️ Fetching full details for volume ID: {volume_id}...{Style.RESET_ALL}")
    url = f"https://comicvine.gamespot.com/api/volume/4050-{volume_id}/"
    params = {
        "api_key": COMICVINE_API_KEY,
        "format": "json",
        "field_list": "id,name,start_year,publisher,description,count_of_issues,image,last_issue,first_issue,characters,teams,locations,concepts"
    }
    headers = {"User-Agent": "ComicOrganizer/1.0"}
    
    response = make_api_request(url, params, headers)
    if response:
        return response.json().get('results')
    return None



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

    response = make_api_request(volume_url, params, headers)
    if response:
        issues = response.json().get('results', {}).get('issues', [])
        issues_map = {issue['issue_number']: issue for issue in issues}
        print(f"{Fore.GREEN}✔ Found and cached {len(issues_map)} issues for this volume.{Style.RESET_ALL}")
        return issues_map
    
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
        "field_list": "name,issue_number,description,cover_date,release_date,volume,person_credits,character_credits,team_credits,location_credits,story_arc_credits,concept_credits,site_detail_url"
    }
    headers = { "User-Agent": "ComicOrganizer/1.0" }

    response = make_api_request(issue_url, params, headers)
    if response:
        issue_details = response.json().get('results')
        if issue_details:
            issue_details['volume'] = volume  # Inject the full volume info
            print(f"{Fore.GREEN}✔ Found issue: {issue_details.get('name') or volume.get('name')} ({issue_details.get('id')}){Style.RESET_ALL}")
            return issue_details
    
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
    headers = { "User-Agent": "ComicOrganizer/1.0" }

    response = make_api_request(search_url, params, headers)
    if not response:
        return None

    results = response.json().get('results', [])
    
    # Always give the user a choice, even if there's only one result
    print(f"{Fore.YELLOW} 👉 Please select the correct series (or provide a URL):{Style.RESET_ALL}")
    for i, res in enumerate(results):
        print(f"    {Fore.CYAN}{i+1}:{Style.RESET_ALL} {res.get('name')} ({res.get('start_year')}) - {Style.DIM}{res.get('site_detail_url')}{Style.RESET_ALL}")
    
    print(f"    {Fore.CYAN}S:{Style.RESET_ALL} Skip this series")
    print(f"    {Fore.CYAN}URL:{Style.RESET_ALL} Paste a direct Comic Vine URL")

    while True:
        choice = input(f"{Fore.YELLOW} 👉 Enter your choice: {Style.RESET_ALL}").strip().lower()
        
        if choice == 's':
            return None
        
        if choice == 'url':
            url = input(f"{Fore.YELLOW} 👉 Paste the Comic Vine URL: {Style.RESET_ALL}").strip()
            # Regex to find the volume ID (e.g., 4050-XXXXX)
            match = re.search(r'/4050-(\d+)/', url)
            if match:
                volume_id = match.group(1)
                print(f"{Fore.CYAN} 🏃‍➡️ Found Volume ID {volume_id} from URL. Fetching details...{Style.RESET_ALL}")
                # Fetch full details directly, bypassing the normal search flow
                return fetch_series_details(volume_id)
            else:
                print(f"{Fore.RED} ✗ Invalid Comic Vine URL format. Please try again.{Style.RESET_ALL}")
                continue

        try:
            choice_num = int(choice)
            if 1 <= choice_num <= len(results):
                return results[choice_num - 1]
            else:
                print(f"{Fore.RED} ✗ Invalid number. Please try again.{Style.RESET_ALL}")
        except ValueError:
            print(f"{Fore.RED} ✗ Invalid input. Please enter a number, 'S', or 'URL'.{Style.RESET_ALL}")






def read_comic_info_from_archive(comic_file_path, overwrite=False):
    """
    Reads ComicInfo.xml from a .cbz archive.
    Returns a tuple: (details, is_complete)
    - details: A dictionary with the parsed data.
    - is_complete: A boolean indicating if the XML has rich metadata.
    """
    if overwrite:
        print(f"{Fore.YELLOW} ⚠️ Overwrite flag is set. Ignoring existing ComicInfo.xml.{Style.RESET_ALL}")
        return None, False

    if not comic_file_path.lower().endswith('.cbz'):
        return None, False

    try:
        with zipfile.ZipFile(comic_file_path, 'r') as zf:
            if 'ComicInfo.xml' not in zf.namelist():
                return None, False
            
            with zf.open('ComicInfo.xml') as xml_file:
                tree = ET.parse(xml_file)
                root = tree.getroot()

                def find_text(tag):
                    element = root.find(tag)
                    return element.text if element is not None and element.text else None

                series = find_text('Series')
                volume_year = find_text('Volume')
                issue_number = find_text('Number')
                
                if not all([series, volume_year, issue_number]):
                    return None, False

                # Check for signs of rich metadata
                publisher = find_text('Publisher')
                summary = find_text('Summary')
                writer = find_text('Writer')
                is_complete = all([publisher, summary, writer])

                # Reconstruct cover_date
                year = find_text('Year')
                month = find_text('Month')
                day = find_text('Day')
                cover_date = None
                if year and month and day:
                    try:
                        cover_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                    except (ValueError, TypeError):
                        pass
                
                details = {
                    'volume': {'name': series, 'start_year': volume_year},
                    'issue_number': issue_number,
                    'cover_date': cover_date,
                }
                
                print(f"{Fore.GREEN} ✔ Found ComicInfo.xml in {os.path.basename(comic_file_path)}. Complete: {is_complete}{Style.RESET_ALL}")
                return details, is_complete

    except (zipfile.BadZipFile, ET.ParseError) as e:
        print(f"{Fore.RED} ✗ Error reading ComicInfo.xml: {e}{Style.RESET_ALL}")
        return None, False
    except Exception as e:
        print(f"{Fore.RED} ✗ Unexpected error parsing ComicInfo.xml: {e}{Style.RESET_ALL}")
        return None, False


def overwrite_comic_info_in_archive(cbz_path, xml_content):
    """
    Safely overwrites the ComicInfo.xml in a .cbz file.
    """
    temp_dir = tempfile.mkdtemp()
    temp_zip_path = os.path.join(temp_dir, os.path.basename(cbz_path))

    try:
        with zipfile.ZipFile(cbz_path, 'r') as original_zip:
            with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as new_zip:
                for item in original_zip.infolist():
                    # Copy all files except the old ComicInfo.xml
                    if item.filename.lower() != 'comicinfo.xml':
                        new_zip.writestr(item, original_zip.read(item.filename))
                # Add the new, enriched ComicInfo.xml
                new_zip.writestr('ComicInfo.xml', xml_content)
        
        # Replace the original file with the new one
        shutil.move(temp_zip_path, cbz_path)
        print(f"{Fore.GREEN} ✔ Successfully overwrote ComicInfo.xml in {os.path.basename(cbz_path)}{Style.RESET_ALL}")
        return True
    except Exception as e:
        print(f"{Fore.RED} ✗ Failed to overwrite ComicInfo.xml: {e}{Style.RESET_ALL}")
        return False
    finally:
        rmtree_with_retry(temp_dir)


def organize_file(original_path, issue_details, output_dir, dry_run=False, version_str=None, skip_xml_write=False):
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
    
    # Construct the new folder path, including the version string if available
    folder_version_str = f" {version_str}" if version_str else ""
    new_series_folder = os.path.join(output_dir, f"{series_name}{folder_version_str} ({volume_year})")
    new_file_path = os.path.join(new_series_folder, new_file_name)

    # Generate ComicInfo.xml
    comic_info_xml = None
    if not skip_xml_write:
        comic_info_xml = generate_comic_info_xml(issue_details)

    if dry_run:
        print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would move and rename to: {new_file_path}{Style.RESET_ALL}")
        if comic_info_xml:
            if skip_xml_write: # This case means we are enriching an existing file
                 print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would overwrite existing ComicInfo.xml with enriched data.{Style.RESET_ALL}")
            else:
                 print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would generate and embed ComicInfo.xml.{Style.RESET_ALL}")

    else:
        print(f"{Fore.CYAN} 📦 Moving and renaming to: {new_file_path}{Style.RESET_ALL}")
        os.makedirs(new_series_folder, exist_ok=True)
        
        if original_path != new_file_path:
            shutil.move(original_path, new_file_path)
        else:
            print(f"{Fore.YELLOW} ⚠️ Skipping move, file already organized.{Style.RESET_ALL}")

        if comic_info_xml:
            if new_file_path.lower().endswith('.cbz'):
                # If we are enriching, we overwrite; otherwise, we add.
                if skip_xml_write:
                    overwrite_comic_info_in_archive(new_file_path, comic_info_xml)
                else:
                    try:
                        with zipfile.ZipFile(new_file_path, 'a') as zf:
                            if 'ComicInfo.xml' not in zf.namelist():
                                zf.writestr('ComicInfo.xml', comic_info_xml)
                                print(f"{Fore.GREEN} ✔ Successfully embedded ComicInfo.xml.{Style.RESET_ALL}")
                            else:
                                # This case should ideally not be hit if logic is correct
                                print(f"{Fore.YELLOW} ⚠️ ComicInfo.xml already exists. Overwriting...{Style.RESET_ALL}")
                                overwrite_comic_info_in_archive(new_file_path, comic_info_xml)
                    except Exception as e:
                        print(f"{Fore.RED} ✗ Error embedding ComicInfo.xml: {e}{Style.RESET_ALL}")

            elif new_file_path.lower().endswith('.cbr'):
                print(f"{Fore.YELLOW} ⚠️ Skipping ComicInfo.xml embedding for .cbr file.{Style.RESET_ALL}")
    
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


def rmtree_with_retry(path, max_retries=5, delay_seconds=0.5):
    """
    Robustly removes a directory tree, retrying on PermissionError.
    """
    for i in range(max_retries):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            print(f"{Fore.YELLOW} ⚠️ Permission denied to remove {path}. Retrying in {delay_seconds}s... ({i+1}/{max_retries}){Style.RESET_ALL}")
            time.sleep(delay_seconds)
        except FileNotFoundError:
            # Directory was already removed, which is fine.
            return
        except Exception as e:
            print(f"{Fore.RED} ✗ Unexpected error while removing {path}: {e}{Style.RESET_ALL}")
            break
    print(f"{Fore.RED} ✗ Failed to remove directory {path} after {max_retries} retries.{Style.RESET_ALL}")


def convert_cbr_to_cbz(cbr_path):
    """
    Converts a .cbr file to a .cbz file, handling misnamed zip files.
    """
    cbz_path = os.path.splitext(cbr_path)[0] + '.cbz'
    
    # Case 1: The file is a ZIP file misnamed as .cbr
    if zipfile.is_zipfile(cbr_path):
        print(f"{Fore.CYAN} 🔄 File is a zip archive. Renaming {cbr_path} to .cbz...{Style.RESET_ALL}")
        try:
            os.rename(cbr_path, cbz_path)
            print(f"{Fore.GREEN} ✔ Successfully renamed to {cbz_path}{Style.RESET_ALL}")
            return cbz_path
        except OSError as e:
            print(f"{Fore.RED} ✗ Error renaming file: {e}{Style.RESET_ALL}")
            return None

    # Case 2: The file is a genuine RAR file
    if is_rarfile(cbr_path):
        temp_dir = tempfile.mkdtemp()
        try:
            print(f"{Fore.CYAN} 🔄 Converting RAR {cbr_path} to .cbz...{Style.RESET_ALL}")
            with RarFile(cbr_path, 'r') as archive:
                archive.extractall(temp_dir)
            
            with zipfile.ZipFile(cbz_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zf.write(file_path, arcname)
            
            with zipfile.ZipFile(cbz_path, 'r') as zf:
                if zf.testzip() is not None:
                    raise Exception("Failed to validate the new .cbz file.")
            
            print(f"{Fore.GREEN} ✔ Successfully converted to {cbz_path}{Style.RESET_ALL}")
            os.remove(cbr_path)
            return cbz_path

        except Exception as e:
            print(f"{Fore.RED} ✗ Error converting {cbr_path}: {e}{Style.RESET_ALL}")
            if os.path.exists(cbz_path):
                os.remove(cbz_path)
            return None
        finally:
            rmtree_with_retry(temp_dir)
    
    # Case 3: The file is not a recognized archive type
    print(f"{Fore.RED} ✗ Skipped: {cbr_path} is not a valid RAR or ZIP file.{Style.RESET_ALL}")
    return None



def main():
    # Parse command line arguments first
    parser = argparse.ArgumentParser(description='Organize comic book files.')
    parser.add_argument('input_dir', nargs='?', default=None, help='The directory containing the comic files to organize. Defaults to the current directory if not specified.')
    parser.add_argument('output_dir', nargs='?', default=None, help='(Optional) The directory to store the organized files. If not provided, organizes in-place.')
    parser.add_argument('--series-folder', help='(Optional) The name of a specific series folder to process within the input directory.')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without moving files.')
    parser.add_argument('--force-refresh', action='store_true', help='Force a refresh of cached data for a specific series folder.')
    parser.add_argument('-o', '--overwrite', action='store_true', help='Treat issues as if they have no metadata, forcing a re-download and overwrite.')
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

{Fore.CYAN} @luccasveg 2025 🏃‍➡️ https://github.com/luccast/Runarr
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

            # Extract version from folder name (e.g., "v1", "v2")
            folder_name = os.path.basename(folder)
            version_match = re.search(r'\b(v\d+)\b', folder_name, re.IGNORECASE)
            version_str = version_match.group(1) if version_match else None

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
            extra_files = [f for f in all_files_in_folder if f not in comic_files_in_folder and not f.lower().endswith('.cbr') and os.path.basename(f).lower() != 'series.json']

            for comic_file in comic_files_in_folder:
                print(f"  {Fore.CYAN}Processing {os.path.basename(comic_file)}...{Style.RESET_ALL}")

                issue_details = None
                skip_xml_write = False
                
                # --- NEW: Prioritize and Assess local ComicInfo.xml ---
                local_details, is_complete = read_comic_info_from_archive(comic_file, overwrite=args.overwrite)
                
                if local_details:
                    if is_complete:
                        # If XML is complete, use it and skip API calls and XML writing
                        print(f"  {Fore.GREEN}✔ Using complete local ComicInfo.xml. Skipping API call.{Style.RESET_ALL}")
                        issue_details = local_details
                        skip_xml_write = True
                    else:
                        # If XML is incomplete, use its data to enrich from the API
                        print(f"  {Fore.YELLOW}⚠️ Incomplete ComicInfo.xml found. Attempting to enrich from API...{Style.RESET_ALL}")
                        # We can reuse the identify_comic function, it will use the series/issue info
                        # and fetch the full details in one go.
                        cover_image = extract_cover_image(comic_file)
                        if cover_image:
                             issue_details = identify_comic(comic_file, cover_image, series_cache, volume_issues_cache, issue_details_cache, base_output_dir, args.dry_run, version_str, overwrite=args.overwrite)
                        # We will NOT skip XML write, as we want to overwrite the incomplete one.
                
                # --- FALLBACK: Use existing API logic if no local XML was found ---
                if not issue_details:
                    cover_image = extract_cover_image(comic_file)
                    if cover_image:
                        issue_details = identify_comic(comic_file, cover_image, series_cache, volume_issues_cache, issue_details_cache, base_output_dir, args.dry_run, version_str, overwrite=args.overwrite)
                    else:
                        print(f"  {Fore.RED} ✗ Could not extract cover image from {os.path.basename(comic_file)}.{Style.RESET_ALL}")

                # --- Organize the file with the determined details ---
                if issue_details:
                    new_file_path = organize_file(comic_file, issue_details, base_output_dir, args.dry_run, version_str, skip_xml_write=skip_xml_write)
                    if new_file_path:
                        processed_comics.add(new_file_path)
                        if not new_series_folder_path:
                            new_series_folder_path = os.path.dirname(new_file_path)

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