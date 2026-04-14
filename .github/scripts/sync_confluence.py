import os
import sys
import hashlib
import re
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
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
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def to_title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()

def markdown_to_storage(md_content: str) -> str:
    """
    Converts Markdown to Confluence Storage Format, with special handling for Mermaid diagrams.
    The mermaid macro uses ac:schema-version=1 as required by Confluence Cloud.
    """
    mermaid_regex = r"```mermaid\n(.*?)\n```"
    parts = re.split(mermaid_regex, md_content, flags=re.DOTALL)
    html_parts = []

    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Regular markdown content
            if part.strip():
                html_parts.append(markdown.markdown(part))
        else:
            # Mermaid diagram part
            diagram_code = part.strip()
            confluence_macro = (
                '<ac:structured-macro ac:name="mermaid" ac:schema-version="1">'
                '<ac:plain-text-body><![CDATA[' + diagram_code + ']]></ac:plain-text-body>'
                '</ac:structured-macro>'
            )
            html_parts.append(confluence_macro)

    return "".join(html_parts)

def find_page_in_space_by_title(title: str):
    try:
        page = confluence.get_page_by_title(
            space=CONFLUENCE_SPACE_KEY, title=title, expand='ancestors,body.storage,version'
        )
        return page
    except Exception:
        return None

def ensure_folder_page(folder_title: str, parent_id: str) -> str:
    existing = find_page_in_space_by_title(folder_title)
    if existing:
        page_id = existing['id']
        current_parent_id = existing['ancestors'][-1]['id'] if existing.get('ancestors') else None
        if str(current_parent_id) != str(parent_id):
            print(f"Moving folder page '{folder_title}' to parent {parent_id}.")
            body_content = existing.get('body', {}).get('storage', {}).get('value', '')
            confluence.update_page(page_id=page_id, title=folder_title, body=body_content, parent_id=parent_id)
        return page_id
    print(f"Creating folder page '{folder_title}' under parent {parent_id}.")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY, parent_id=parent_id, title=folder_title, body="", representation="storage"
    )
    return created["id"]

def main():
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Error: Missing required Confluence environment variables.")
        sys.exit(1)

    print(f"Starting sync: Markdown files from '{DOCS_FOLDER}' to Confluence space '{CONFLUENCE_SPACE_KEY}'.")

    # --- 2. Build Confluence Folder Hierarchy ---
    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
            for d in dirs:
                sub_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_path in folder_parent_ids:
                    continue
                folder_page_id = ensure_folder_page(to_title(d), parent_id)
                folder_parent_ids[sub_path] = folder_page_id
    else:
        print(f"Warning: '{DOCS_FOLDER}' directory not found.")

    # --- 3. Discover Local Markdown Files ---
    local_pages = {}
    if os.path.isdir(DOCS_FOLDER):
        for root, _, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
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
                key = (parent_id, title)
                local_pages[key] = {
                    "title": title,
                    "storage": storage,
                    "hash": md5(storage),
                    "parent_id": parent_id,
                    "filepath": filepath,
                }

    # --- 4. Fetch All Existing Confluence Pages ---
    all_existing_pages = {}
    start = 0
    limit = 200
    while True:
        try:
            pages_chunk = confluence.get_all_pages_from_space(
                CONFLUENCE_SPACE_KEY, start=start, limit=limit, expand='ancestors,body.storage,version'
            )
            if not pages_chunk:
                break
            for page in pages_chunk:
                parent_id = page['ancestors'][-1]['id'] if page.get('ancestors') else None
                all_existing_pages[(parent_id, page['title'])] = {
                    "id": page['id'],
                    "title": page['title'],
                    "parent_id": parent_id,
                    "hash": md5(page.get("body", {}).get("storage", {}).get("value", "")),
                    "version": page.get("version", {}).get("number", 1),
                }
            if len(pages_chunk) < limit:
                break
            start += limit
        except Exception as e:
            print(f"Error fetching pages: {e}")
            sys.exit(1)

    # --- 5. Determine Actions ---
    pages_to_create = []
    pages_to_update = []
    pages_to_delete = []

    for key, local in local_pages.items():
        remote = all_existing_pages.get(key)
        if not remote:
            existing_anywhere = find_page_in_space_by_title(local["title"])
            if existing_anywhere:
                pages_to_update.append({"id": existing_anywhere["id"], **local, "action": "move"})
            else:
                pages_to_create.append(local)
        elif local["hash"] != remote["hash"]:
            pages_to_update.append({"id": remote["id"], **local, "action": "update"})
        else:
            print(f"Up to date: {local['filepath']} -> '{local['title']}'")

    for remote_key, remote in all_existing_pages.items():
        page_id = remote["id"]
        if str(page_id) == str(CONFLUENCE_PARENT_PAGE_ID):
            continue
        if remote_key not in local_pages:
            is_folder = page_id in folder_parent_ids.values()
            if is_folder and confluence.get_child_pages(page_id):
                print(f"Skipping deletion of FOLDER page '{remote['title']}' (ID {page_id}) as it has children.")
                continue
            pages_to_delete.append(remote)

    # --- 6. Execute Actions ---
    for p in pages_to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']}.")
        try:
            confluence.create_page(
                space=CONFLUENCE_SPACE_KEY, parent_id=p["parent_id"],
                title=p["title"], body=p["storage"], representation="storage"
            )
        except Exception as e:
            print(f"Error creating page '{p['title']}': {e}")

    for p in pages_to_update:
        print(f"Updating page '{p['title']}' (ID {p['id']}) - Action: {p['action']}.")
        try:
            confluence.update_page(
                page_id=p["id"], title=p["title"], body=p["storage"], parent_id=p["parent_id"]
            )
        except Exception as e:
            print(f"Error updating page '{p['title']}': {e}")

    for p in pages_to_delete:
        print(f"Deleting page '{p['title']}' (ID {p['id']}).")
        try:
            confluence.remove_page(page_id=p["id"], recursive=False)
        except Exception as e:
            print(f"Error deleting page '{p['title']}': {e}")

    # --- 7. Summary ---
    print("\n========== Sync Summary ==========")
    print(f"Pages created  : {len(pages_to_create)}")
    print(f"Pages updated  : {len(pages_to_update)}")
    print(f"Pages deleted  : {len(pages_to_delete)}")
    print("===================================")
    print("Sync complete.")


if __name__ == "__main__":
    main()
