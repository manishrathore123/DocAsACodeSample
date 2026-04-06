import os
import sys
import hashlib
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONfluence_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')

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
    """
    Finds a page in the Confluence space by its title.
    Returns the page details if found, else None.
    """
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
    """
    Ensures a Confluence page exists with the given folder_title under parent_id.
    Returns the ID of the folder page.
    """
    existing = find_page_in_space_by_title(folder_title)
    if existing:
        page_id = existing['id']
        current_parent_id = existing['ancestors'][-1]['id'] if existing.get('ancestors') else None
        if str(current_parent_id) != str(parent_id):
            print(f"Moving existing folder page '{folder_title}' (ID {page_id}) to be under parent {parent_id}.")
            body_content = existing.get('body', {}).get('storage', {}).get('value', '')
            confluence.update_page(
                page_id=page_id,
                title=folder_title,
                body=body_content,
                parent_id=parent_id,
            )
        return page_id

    print(f"Creating new folder page '{folder_title}' under parent {parent_id}.")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY,
        parent_id=parent_id,
        title=folder_title,
        body="",
        representation="storage",
    )
    return created["id"]

def main():
    # --- 1. Initial Checks ---
    if not all([
        CONFLUENCE_URL,
        CONFLUENCE_USERNAME,
        CONFLUENCE_API_TOKEN,
        CONFLUENCE_SPACE_KEY,
        CONFLUENCE_PARENT_PAGE_ID,
    ]):
        print("Error: Missing required Confluence environment variables.")
        sys.exit(1)

    print(f"Starting sync: Markdown files from '{DOCS_FOLDER}' to Confluence space '{CONFLUENCE_SPACE_KEY}'.")

    # --- 2. Build Confluence Folder Hierarchy ---
    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}

    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            current_confluence_parent_id = folder_parent_ids[folder_path]

            for d in dirs:
                sub_folder_relative_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_folder_relative_path in folder_parent_ids:
                    continue
                folder_title = to_title(d)
                folder_page_id = ensure_folder_page(folder_title, current_confluence_parent_id)
                folder_parent_ids[sub_folder_relative_path] = folder_page_id
    else:
        print(f"Warning: '{DOCS_FOLDER}' directory not found.")

    # --- 3. Discover Local Markdown Files ---
    local_markdown_pages = {}
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            confluence_parent_id_for_current_folder = folder_parent_ids[folder_path]

            for filename in files:
                if not filename.endswith(".md"):
                    continue
                filepath = os.path.join(root, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    md_content = f.read()

                name_no_ext = os.path.splitext(filename)[0]
                if folder_path == "" and name_no_ext.lower() == "index":
                    title = "Documentation Home"
                elif name_no_ext.lower() == "index":
                    title = to_title(os.path.basename(folder_path))
                else:
                    title = to_title(name_no_ext)

                storage = markdown_to_storage(md_content)
                content_hash = md5(storage)

                key = (confluence_parent_id_for_current_folder, title)
                local_markdown_pages[key] = {
                    "title": title,
                    "storage": storage,
                    "hash": content_hash,
                    "parent_id": confluence_parent_id_for_current_folder,
                    "filepath": filepath,
                }

    # --- 4. Fetch ALL existing pages in the Confluence space ---
    all_existing_confluence_pages_by_key = {}
    start = 0
    limit = 200
    while True:
        try:
            pages_chunk = confluence.get_all_pages_from_space(
                CONFLUENCE_SPACE_KEY, start=start, limit=limit, expand='ancestors,body.storage,version'
            )
            if not pages_chunk: break
            for page in pages_chunk:
                parent_id = page['ancestors'][-1]['id'] if page.get('ancestors') else None
                all_existing_confluence_pages_by_key[(parent_id, page['title'])] = {
                    "id": page['id'],
                    "title": page['title'],
                    "parent_id": parent_id,
                    "hash": md5(page.get('body', {}).get('storage', {}).get('value', '')),
                    "version": page.get('version', {}).get('number', 1),
                }
            if len(pages_chunk) < limit: break
            start += limit
        except Exception as e:
            print(f"Error fetching all pages from space: {e}")
            sys.exit(1)

    # --- 5. Determine Actions ---
    pages_to_create = []
    pages_to_update_or_move = []
    pages_to_delete = []

    for key, local_info in local_markdown_pages.items():
        expected_parent_id = key[0]
        title = key[1]
        existing_in_correct_place = all_existing_confluence_pages_by_key.get(key)
        existing_anywhere_by_title = find_page_in_space_by_title(title)

        if not existing_in_correct_place and not existing_anywhere_by_title:
            pages_to_create.append(local_info)
        else:
            remote_info = existing_in_correct_place or existing_anywhere_by_title
            needs_move = str(remote_info['parent_id']) != str(expected_parent_id)
            needs_update = local_info['hash'] != remote_info['hash']
            if needs_move or needs_update:
                pages_to_update_or_move.append({
                    "id": remote_info['id'],
                    "title": local_info['title'],
                    "storage": local_info['storage'],
                    "filepath": local_info['filepath'],
                    "target_parent_id": expected_parent_id,
                })
            else:
                print(f"Up to date: {local_info['filepath']} -> '{title}'")

    for remote_key, remote_info in all_existing_confluence_pages_by_key.items():
        page_id = remote_info['id']
        if str(page_id) == str(CONFLUENCE_PARENT_PAGE_ID):
            continue

        if remote_key not in local_markdown_pages.keys():
            is_managed_folder_page = page_id in folder_parent_ids.values()
            children_of_this_page = confluence.get_child_pages(page_id)

            # FIX: Only apply the children check to FOLDER pages
            if is_managed_folder_page and children_of_this_page:
                print(f"Skipping deletion of FOLDER page '{remote_info['title']}' (ID {page_id}) as it still contains children.")
                continue
            
            pages_to_delete.append(remote_info)

    # --- 6. Execute Actions ---
    for p in pages_to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']} from {p['filepath']}.")
        try:
            confluence.create_page(space=CONFLUENCE_SPACE_KEY, parent_id=p["parent_id"], title=p["title"], body=p["storage"], representation="storage")
        except Exception as e:
            print(f"Error creating page '{p['title']}': {e}")

    for p in pages_to_update_or_move:
        print(f"Updating page '{p['title']}' (ID {p['id']}) from {p['filepath']}.")
        try:
            confluence.update_page(page_id=p["id"], title=p["title"], body=p["storage"], parent_id=p["target_parent_id"])
        except Exception as e:
            print(f"Error updating page '{p['title']}' (ID {p['id']}): {e}")

    for p in pages_to_delete:
        print(f"Deleting page '{p['title']}' (ID {p['id']}) as it no longer exists in Git repo.")
        try:
            confluence.remove_page(page_id=p["id"], recursive=False)
        except Exception as e:
            print(f"Error deleting page '{p['title']}' (ID {p['id']}): {e}")

    # --- 7. Summary ---
    print("\n========== Sync Summary ==========")
    print(f"Pages created  : {len(pages_to_create)}")
    print(f"Pages updated  : {len(pages_to_update_or_move)}")
    print(f"Pages deleted  : {len(pages_to_delete)}")
    print("===================================")
    print("Sync complete.")

if __name__ == "__main__":
    main()
