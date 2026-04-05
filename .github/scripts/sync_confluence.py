import os
import sys
import hashlib
import json
from atlassian import Confluence
import markdown # For converting Markdown to HTML

# --- Configuration ---
# These variables will be loaded from GitHub Secrets (environment variables)
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME') # For Confluence Cloud, this is your email
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')

DOCS_FOLDER = 'docs' # The folder in your Git repo containing Markdown files

# --- Initialize Confluence API client ---
# For Confluence Cloud, set cloud=True and use username (email) and API token.
# For Confluence Server, set cloud=False and use username and password.
# The 'password' parameter is used for the API token for Cloud.
confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USERNAME,
    password=CONFLUENCE_API_TOKEN,
    cloud=True # Set to False for Confluence Server, True for Confluence Cloud
)

def generate_page_title(filepath):
    """Generates a Confluence page title from a Markdown file path."""
    # Example: docs/my-folder/my-page.md -> My Page
    # If it's index.md, use the parent folder name or a default "Documentation Home"
    
    relative_path = os.path.relpath(filepath, DOCS_FOLDER)
    parts = relative_path.split(os.sep)
    
    if parts[-1].lower() == "index.md":
        # If it's an index.md, try to use the parent folder name
        if len(parts) > 1:
            title = parts[-2]
        else: # It's docs/index.md, directly in the root of the docs folder
            title = "Documentation Home" # A default title for the main index
    else:
        # For other files, use the filename without extension
        title = os.path.splitext(parts[-1])[0]

    # Clean up and title-case the title
    return title.replace('-', ' ').replace('_', ' ').title()

def get_content_hash(content):
    """Generates an MD5 hash of the content to detect changes."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def get_all_confluence_pages_under_parent(parent_page_id):
    """Fetches all child pages under a given parent page and their content hash."""
    print(f"Fetching existing Confluence pages under parent ID: {parent_page_id}")
    all_pages = {}
    
    try:
        # Corrected method: confluence.get_child_pages
        # This method directly returns a list of page dictionaries.
        children_list = confluence.get_child_pages(parent_page_id, limit=200) 
        
        for page in children_list: # children_list now directly contains the page dictionaries
            page_id = page['id']
            page_title = page['title']
            
            # Fetch the actual content of each child page to get its storage format
            # This is a network call for each page, can be slow for many pages.
            page_content_detail = confluence.get_page_by_id(page_id, expand='body.storage')
            
            storage_hash = None
            if page_content_detail and 'body' in page_content_detail and 'storage' in page_content_detail['body']:
                storage_format_content = page_content_detail['body']['storage']['value']
                storage_hash = get_content_hash(storage_format_content)
            else:
                print(f"Warning: Could not retrieve storage content for Confluence page '{page_title}' ({page_id}). Hash will be null.")

            all_pages[page_title] = {
                'id': page_id,
                'hash': storage_hash,
                'title': page_title
            }
        print(f"Found {len(all_pages)} child pages in Confluence under parent ID {parent_page_id}.")
    except Exception as e:
        print(f"Error fetching Confluence pages under parent ID {parent_page_id}: {e}")
        # In a GA, it's often better to exit on critical errors like this.
        sys.exit(1) 

    return all_pages

def main():
    # Check if all required environment variables are set
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Error: Missing one or more Confluence environment variables.")
        print("Please ensure CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, and CONFLUENCE_PARENT_PAGE_ID are set as GitHub Secrets.")
        sys.exit(1)

    print(f"Attempting to sync Markdown files from '{DOCS_FOLDER}' to Confluence space '{CONFLUENCE_SPACE_KEY}' under parent page ID '{CONFLUENCE_PARENT_PAGE_ID}'")

    # Get existing pages in Confluence under the parent
    existing_confluence_pages = get_all_confluence_pages_under_parent(CONFLUENCE_PARENT_PAGE_ID)
    
    local_md_files = {}
    pages_to_create = []
    pages_to_update = []
    
    # Discover local Markdown files and prepare their content for Confluence
    if not os.path.exists(DOCS_FOLDER):
        print(f"Warning: '{DOCS_FOLDER}' directory not found. No Markdown files to process.")
        # If docs folder is missing, we might only need to handle deletions from Confluence.
        # But if there are no local files, then all existing Confluence pages might be marked for deletion.
    else:
        for root, _, files in os.walk(DOCS_FOLDER):
            for filename in files:
                if filename.endswith('.md'):
                    filepath = os.path.join(root, filename)
                    with open(filepath, 'r', encoding='utf-8') as f:
                        md_content = f.read()
                    
                    page_title = generate_page_title(filepath)
                    
                    # Convert Markdown to HTML for Confluence storage format
                    # The 'markdown' library does a good job, and Confluence typically handles basic HTML.
                    # A wrapper div can sometimes help Confluence render it better.
                    html_content = markdown.markdown(md_content)
                    storage_format = f'<div class="markdown-body">{html_content}</div>'
                    
                    local_md_files[page_title] = {
                        'filepath': filepath,
                        'storage_format': storage_format,
                        'storage_hash': get_content_hash(storage_format)
                    }

    # --- Determine Actions (Create/Update/Delete) ---

    # Pages to Create or Update
    for title, local_page_data in local_md_files.items():
        if title not in existing_confluence_pages:
            pages_to_create.append(local_page_data)
        else:
            confluence_page_id = existing_confluence_pages[title]['id']
            confluence_page_hash = existing_confluence_pages[title]['hash']
            
            # Compare hashes of the Confluence storage format (local MD converted to storage format)
            # with the actual Confluence page's storage format.
            if local_page_data['storage_hash'] != confluence_page_hash:
                pages_to_update.append({
                    'id': confluence_page_id,
                    'title': title,
                    **local_page_data # Include all local data
                })
            else:
                print(f"Confluence page '{title}' is up to date (hash match). Skipping update.")

    # Pages to Delete (exist in Confluence but not in local files)
    pages_to_delete = []
    for title, page_info in existing_confluence_pages.items():
        if title not in local_md_files:
            pages_to_delete.append({
                'id': page_info['id'],
                'title': title
            })

    # --- Execute Actions ---

    # 1. Create new pages
    for page_data in pages_to_create:
        print(f"Creating new Confluence page: '{page_data['title']}' from '{page_data['filepath']}'")
        try:
            confluence.create_page(
                space=CONFLUENCE_SPACE_KEY,
                parent_id=CONFLUENCE_PARENT_PAGE_ID,
                title=page_data['title'],
                body=page_data['storage_format'],
                representation='storage'
            )
            print(f"Successfully created '{page_data['title']}'.")
        except Exception as e:
            print(f"Error creating Confluence page '{page_data['title']}': {e}")
            # Do not exit here to allow other operations to proceed, but log the error
            # For a critical system, you might want to uncomment sys.exit(1)

    # 2. Update existing pages
    for page_data in pages_to_update:
        print(f"Updating Confluence page: '{page_data['title']}' (ID: {page_data['id']}) from '{page_data['filepath']}'")
        try:
            # Need to get current version to update a page
            current_page_info = confluence.get_page_by_id(page_data['id'], expand='version')
            current_version = current_page_info['version']['number'] if 'version' in current_page_info else 1

            confluence.update_page(
                page_id=page_data['id'],
                title=page_data['title'],
                body=page_data['storage_format'],
                representation='storage',
                version=current_version + 1 # Increment version number for the update
            )
            print(f"Successfully updated '{page_data['title']}'.")
        except Exception as e:
            print(f"Error updating Confluence page '{page_data['title']}' (ID: {page_data['id']}): {e}")
            # sys.exit(1)

    # 3. Delete pages (THIS IS THE PART THAT WAS MISSING/CUT OFF)
    for page_data in pages_to_delete:
        print(f"Deleting Confluence page: '{page_data['title']}' (ID: {page_data['id']}) because no corresponding local Markdown file was found.")
        try:
            # Important: recursive=False to only delete the specified page, not its children.
            # If you want to delete children too, set recursive=True, but be very careful as this is destructive!
            confluence.remove_page(page_id=page_data['id'], recursive=False)
            print(f"Successfully deleted '{page_data['title']}'.")
        except Exception as e:
            print(f"Error deleting Confluence page '{page_data['title']}' (ID: {page_data['id']}): {e}")
            # Do not exit here, as deletion failures for one page shouldn't stop others

    if not pages_to_create and not pages_to_update and not pages_to_delete:
        print("No changes detected in Markdown files. Confluence pages are in sync.")
    else:
        print("Confluence synchronization process completed.")

if __name__ == "__main__":
    main()
