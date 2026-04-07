import os
import sys
import hashlib
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')
CONFLUENCE_ARCHIVE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_ARCHIVE_PARENT_PAGE_ID') # For archiving

DOCS_FOLDER = "docs"

confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USERNAME,
    password=CONFLUENCE_API_TOKEN,
    cloud=True,
)

def md5(text: str) -> str:
    """Generates an MD5 hash of the content for change detection."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def to_title(name: str) -> str:
    """Converts a file/folder name into a Confluence-friendly title."""
    return name.replace("-", " ").replace("_", " ").strip().title()

def markdown_to_storage(md_content: str) -> str:
    """Converts Markdown content to Confluence storage format (HTML)."""
    html = markdown.markdown(md_content)
    return f'<div class="markdown-body">{html}</div>'

def find_page_in_space_by_title(title: str):
    """Finds a page in the Confluence space by its title."""
    try:
        page = confluence.get_page_by_title(
            space=CONFLUENCE_SPACE_KEY,
            title=title,
            expand='ancestors,body.storage,version'
        )
        return page
    except Exception:
        return None

def ensure_folder_page(folder_title: str, parent_id: str) -> str:
    """Ensures a Confluence page exists for a folder, moving it if necessary."""
    existing = find_page_in_space_by_title(folder_title)
    if existing:
        page_id = existing['id']
        current_parent_id = existing['ancestors'][-1]['id'] if existing.get('ancestors') else None
        if str(current_parent_id) != str(parent_id):
            print(f"Moving existing folder page '{folder_title}' (ID {page_id}) to be under parent {parent_id}.")
            body_content = existing.get('body', {}).get('storage', {}).get('value', '')
            confluence.update_page(
                page_id=page_id, title=folder_title, body=body_content, parent_id=parent_id
            )
        return page_id

    print(f"Creating new folder page '{folder_title}' under parent {parent_id}.")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY, parent_id=parent_id, title=folder_title, body="", representation="storage"
    )
    return created["id"]

def main():
    # --- 1. Initial Checks ---
    if not all([
        CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID
    ]):
        print("Error: Missing required Confluence environment variables."); sys.exit(1)

    print(f"Starting sync: Markdown files from '{DOCS_FOLDER}' to Confluence space '{CONFLUENCE_SPACE_KEY}'.")
    if CONFLUENCE_ARCHIVE_PARENT_PAGE_ID:
        print(f"Archiving enabled: Pages no longer in '{DOCS_FOLDER}' will be moved to parent ID '{CONFLUENCE_ARCHIVE_PARENT_PAGE_ID}'.")
        try:
            confluence.get_page_by_id(CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)
        except Exception:
            print(f"Error: Confluence archive parent page ID '{CONFLUENCE_ARCHIVE_PARENT_PAGE_ID}' does not exist or is inaccessible."); sys.exit(1)
    else:
        print(f"Archiving disabled: Pages no longer in '{DOCS_FOLDER}' will be deleted (moved to Confluence trash).")

    # --- 2. Build Confluence Folder Hierarchy ---
    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            current_parent_id = folder_parent_ids[folder_path]
            for d in dirs:
                sub_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_path in folder_parent_ids: continue
                folder_title = to_title(d)
                folder_page_id = ensure_folder_page(folder_title, current_parent_id)
                folder_parent_ids[sub_path] = folder_page_id

    # --- 3. Discover Local Markdown Files ---
    local_markdown_pages = {}
    if os.path.isdir(DOCS_FOLDER):
        for root, _, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
            for filename in files:
                if not filename.endswith(".md"): continue
                filepath = os.path.join(root, filename)
                with open(filepath, "r", encoding="utf-8") as f: md_content = f.read()
                name_no_ext = os.path.splitext(filename)[0]
                title = (
                    "Documentation Home" if folder_path == "" and name_no_ext.lower() == "index" else
                    to_title(os.path.basename(folder_path)) if name_no_ext.lower() == "index" else
                    to_title(name_no_ext)
                )
                key = (parent_id, title)
                local_markdown_pages[key] = {"title": title, "storage": markdown_to_storage(md_content), "hash": md5(markdown_to_storage(md_content)), "parent_id": parent_id, "filepath": filepath}

    # --- 4. Fetch ALL existing pages for comparison ---
    all_confluence_pages = {}
    start, limit = 0, 200
    while True:
        try:
            pages_chunk = confluence.get_all_pages_from_space(CONFLUENCE_SPACE_KEY, start=start, limit=limit, expand='ancestors,body.storage,version')
            if not pages_chunk: break
            for page in pages_chunk:
                parent_id = page['ancestors'][-1]['id'] if page.get('ancestors') else None
                all_confluence_pages[(parent_id, page['title'])] = {"id": page['id'], "title": page['title'], "parent_id": parent_id, "hash": md5(page.get('body',{}).get('storage',{}).get('value',''))}
            if len(pages_chunk) < limit: break
            start += limit
        except Exception as e:
            print(f"Error fetching pages: {e}"); sys.exit(1)

    # --- 5. Determine Actions ---
    to_create, to_update, to_archive_or_delete = [], [], []
    for key, local in local_markdown_pages.items():
        remote = all_confluence_pages.get(key)
        if not remote:
            # If not in correct place, check if it exists anywhere else to be moved
            existing_anywhere = find_page_in_space_by_title(local['title'])
            if existing_anywhere:
                to_update.append({"id": existing_anywhere['id'], **local, "action": "move"})
            else:
                to_create.append(local)
        elif local['hash'] != remote['hash']:
            to_update.append({"id": remote['id'], **local, "action": "update"})
        else:
            print(f"Up to date: {local['filepath']} -> '{local['title']}'")

    for remote_key, remote_info in all_confluence_pages.items():
        page_id, title = remote_info['id'], remote_info['title']
        # SAFETY CHECKS
        if str(page_id) == str(CONFLUENCE_PARENT_PAGE_ID) or            (CONFLUENCE_ARCHIVE_PARENT_PAGE_ID and str(page_id) == str(CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)):
            continue
        if remote_key not in local_markdown_pages.keys():
            is_managed_folder = page_id in folder_parent_ids.values()
            if is_managed_folder and confluence.get_child_pages(page_id):
                print(f"Skipping deletion of FOLDER page '{title}' (ID {page_id}) as it still has children.")
                continue
            to_archive_or_delete.append(remote_info)

    # --- 6. Execute Actions ---
    for p in to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']} from {p['filepath']}.")
        try: confluence.create_page(space=CONFLUENCE_SPACE_KEY, parent_id=p["parent_id"], title=p["title"], body=p["storage"], representation="storage")
        except Exception as e: print(f"Error creating page '{p['title']}': {e}")

    for p in to_update:
        action_str = "Moving and updating" if p['action'] == "move" else "Updating content for"
        print(f"{action_str} page '{p['title']}' (ID {p['id']}) from {p['filepath']}.")
        try: confluence.update_page(page_id=p["id"], title=p["title"], body=p["storage"], parent_id=p["parent_id"])
        except Exception as e: print(f"Error updating page '{p['title']}': {e}")

    for p in to_archive_or_delete:
        if CONFLUENCE_ARCHIVE_PARENT_PAGE_ID:
            print(f"Archiving page '{p['title']}' (ID {p['id']}) by moving to parent '{CONFLUENCE_ARCHIVE_PARENT_PAGE_ID}'.")
            try:
                current_body = confluence.get_page_by_id(p['id'], expand='body.storage')['body']['storage']['value']
                confluence.update_page(page_id=p["id"], title=p["title"], body=current_body, parent_id=CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)
            except Exception as e: print(f"Error archiving page '{p['title']}': {e}")
        else:
            print(f"Deleting page '{p['title']}' (ID {p['id']}) as it no longer exists in Git repo.")
            try: confluence.remove_page(page_id=p["id"], recursive=False)
            except Exception as e: print(f"Error deleting page '{p['title']}': {e}")

    # --- 7. Summary ---
    print("\n========== Sync Summary ==========")
    print(f"Pages created  : {len(to_create)}")
    print(f"Pages updated  : {len(to_update)}")
    print(f"Pages archived/deleted : {len(to_archive_or_delete)}")
    print("===================================")
    print("Sync complete.")

if __name__ == "__main__":
    main()
