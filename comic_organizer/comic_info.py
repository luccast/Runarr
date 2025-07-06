import xml.etree.ElementTree as ET
from datetime import datetime

def generate_comic_info_xml(issue_details):
    """
    Generates the ComicInfo.xml content as a string, including rich metadata.
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
    add_element(root, 'Volume', volume_info.get('start_year')) # Year is used as Volume for grouping in some readers
    add_element(root, 'Publisher', volume_info.get('publisher', {}).get('name'))
    add_element(root, 'Web', issue_details.get('site_detail_url'))

    # Add summary, handling potential HTML
    summary = issue_details.get('description')
    if summary:
        summary_el = ET.SubElement(root, 'Summary')
        summary_el.text = summary  # The XML library handles escaping

    # Add date fields, preferring release_date over cover_date
    date_str = issue_details.get('release_date') or issue_details.get('cover_date')
    if date_str:
        try:
            # Dates are expected in 'YYYY-MM-DD' format
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
            add_element(root, 'Year', parsed_date.year)
            add_element(root, 'Month', parsed_date.month)
            add_element(root, 'Day', parsed_date.day)
        except (ValueError, TypeError):
            pass # Ignore if date format is invalid

    # Add credits
    add_element(root, 'Writer', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'writer' in p['role'].lower()])))
    add_element(root, 'Penciller', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'penciller' in p['role'].lower()])))
    add_element(root, 'Inker', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'inker' in p['role'].lower()])))
    add_element(root, 'Colorist', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'colorist' in p['role'].lower()])))
    add_element(root, 'Letterer', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'letterer' in p['role'].lower()])))
    add_element(root, 'CoverArtist', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'cover' in p['role'].lower()])))
    add_element(root, 'Editor', ', '.join(sorted([p['name'] for p in issue_details.get('person_credits', []) if 'editor' in p['role'].lower()])))
    
    # Add rich metadata
    add_element(root, 'Genre', ', '.join(sorted([c['name'] for c in issue_details.get('concept_credits', [])])))
    add_element(root, 'Characters', ', '.join(sorted([c['name'] for c in issue_details.get('character_credits', [])])))
    add_element(root, 'Teams', ', '.join(sorted([t['name'] for t in issue_details.get('team_credits', [])])))
    add_element(root, 'Locations', ', '.join(sorted([l['name'] for l in issue_details.get('location_credits', [])])))
    add_element(root, 'StoryArc', ', '.join(sorted([sa['name'] for sa in issue_details.get('story_arc_credits', [])])))

    # Convert the XML tree to a string
    return ET.tostring(root, encoding='unicode')
