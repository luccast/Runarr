import json
import os
import re
from datetime import datetime
from colorama import Fore, Style

def generate_series_data(series_details):
    """Generates the metadata dictionary for a series.json file."""
    if not series_details:
        return None

    # Determine series status
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

    # Determine publication run
    pub_run = series_details.get('start_year', '')
    if last_issue and last_issue.get('cover_date'):
        try:
            last_year = datetime.strptime(last_issue['cover_date'], '%Y-%m-%d').year
            if str(last_year) != pub_run:
                pub_run += f" - {last_year}"
        except (ValueError, TypeError):
            pass

    # Determine book type
    booktype = 'Standard'
    if series_details.get('count_of_issues') == 1:
        booktype = 'One-Shot'

    # Clean up description
    description_html = series_details.get('description', '') or ''
    description_text = re.sub(r'<[^>]+>', '', description_html).strip()

    # Construct the metadata dictionary
    metadata = {
        'version': '1.0.3',
        'metadata': {
            'type': 'comicSeries',
            'publisher': series_details.get('publisher', {}).get('name'),
            'imprint': series_details.get('publisher', {}).get('name'), # Assuming imprint is same as publisher for now
            'name': series_details.get('name'),
            'comicid': series_details.get('id'),
            'year': int(series_details['start_year']) if series_details.get('start_year') else None,
            'description_text': description_text,
            'description_formatted': description_html,
            'volume': None, # Placeholder
            'booktype': booktype,
            'age_rating': None, # Placeholder
            'collects': None, # Placeholder
            'comic_image': series_details.get('image', {}).get('original_url'),
            'total_issues': series_details.get('count_of_issues'),
            'publication_run': pub_run,
            'status': series_status,
            'characters': sorted([char['name'] for char in series_details.get('characters', [])]),
            'teams': sorted([team['name'] for team in series_details.get('teams', [])]),
            'locations': sorted([loc['name'] for loc in series_details.get('locations', [])]),
            'concepts': sorted([concept['name'] for concept in series_details.get('concepts', [])]) # Often used for genre
        }
    }
    return metadata

def write_series_json(series_data, series_folder, dry_run):
    """Writes the series metadata to a series.json file."""
    if not series_data:
        return

    if not dry_run:
        try:
            os.makedirs(series_folder, exist_ok=True)
            series_json_path = os.path.join(series_folder, 'series.json')
            with open(series_json_path, 'w', encoding='utf-8') as f:
                json.dump(series_data, f, indent=4, ensure_ascii=False)
            print(f"{Fore.GREEN}✔ Successfully wrote series.json to: {series_folder}{Style.RESET_ALL}")
        except IOError as e:
            print(f"{Fore.RED} ✗ Error writing series.json: {e}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN} 🏃‍➡️ [DRY RUN] Would write series.json to: {os.path.join(series_folder, 'series.json')}{Style.RESET_ALL}")
        # print(json.dumps(series_data, indent=4))
