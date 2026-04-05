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

DOCS_FOLDER = 'docs'  # root folder in repo

# --- Confluence client ---
confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USERNAME,
    password=CONFLUENCE_API_TOKEN,
    cloud=True
)

# ---------- Helpers ----------

def md5(content: str) -> str:
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def to_title(name: str) -> str:
    """Convert folder/file name to human title."""
    return (
        name.replace('-', ' ')
            .replace('_', ' ')
            .strip()
            .title()
    )

def get_or_create_page(title: str, parent_id: str):
    """
    Get a page by title under a specific parent. If it doesn't exist, create it.
    Return page dict {id, title, hash}.
    """
    # Search children of parent by title
    children = confluence.get_child_pages(parent_id)
    for c in children:
        if c['title'] == title:
            # fetch content to compute hash
            detail = confluence.get_page_by_id(c['id'], expand='body.storage')
            storage = detail.get('body', {}).get('storage', {}).get('value', '')
            return {
                'id': c['id'],
                'title': c['title'],
                'hash': md5(storage)
            }

    # doesn't exist: create empty page
    print(f"Creating folder/parent page '{title}' under parent {parent_id}")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY,
        parent_id=parent_id,
        title=title,
        body='',  # empty for now
        representation='storage'
    )
    return {
        'id': created['id'],
        'title': created['title'],
        'hash': md5('')
    }

def get_existing_tree(root_parent_id: str):
    """
    Build a map of all existing pages under the root parent, by (path_key -> page_info).
    path_key is a string like 'docs', 'docs/guide', 'docs/guide/part-1'.
    """
    tree = {}

    def walk(parent_id: str, current_path: str):
        children = confluence.get_child_pages(parent_id)
        for c in children:
            title = c['title']
            # path_key is purely conceptual; we use titles joined with '/'
            path_key = f"{current_path}/{title}" if current_path else title

            detail = confluence.get_page_by_id(c['id'], expand='body.storage')
            storage = detail.get('body', {}).get('storage', {}).get('value', '')
            page_hash = md5(storage)

            tree[path_key] = {
                'id': c['id'],
                'title': title,
                'hash': page_hash,
                'parent_path': current_path,
                'parent_id': parent_id
            }

            # recurse
            walk(c['id'], path_key)

    # root path is like 'docs-root'
    root_path_key = 'ROOT'
    walk(root_parent_id, root_path_key)
    return tree

# ---------- Main ----------

def main():
    # sanity check
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN,
                CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Missing one or more Confluence env vars.")
        sys.exit(1)

    print(f"Syncing '{DOCS_FOLDER}' into Confluence space '{CONFLUENCE_SPACE_KEY}' "
          f"under parent page ID '{CONFLUENCE_PARENT_PAGE_ID}'")

    # 1. Get existing tree from Confluence
    existing_tree = get_existing_tree(CONFLUENCE_PARENT_PAGE_ID)

    # We'll key local paths as "ROOT/docs[/subdir]/Title"
    local_pages = {}  # path_key -> {title, storage, hash, parent_path}

    # 2. Walk docs/ and compute titles + parents
    if not os.path.exists(DOCS_FOLDER):
        print(f"Warning: '{DOCS_FOLDER}' not found.")
    else:
        for root, dirs, files in os.walk(DOCS_FOLDER):
            # relative folder from docs
            rel_dir = os.path.relpath(root, DOCS_FOLDER)  # '.' or 'guide' or 'guide/sub'
            # Build folder path as titles
            if rel_dir == '.':
                folder_parts = []  # docs root itself
            else:
                folder_parts = rel_dir.split(os.sep)

            # Parent path key in Confluence terms (titles joined with '/')
            # Root parent in Confluence mapping is 'ROOT'
            parent_path_key = 'ROOT'
            parent_id = CONFLUENCE_PARENT_PAGE_ID

            # Ensure folder pages (for each level)
            for part in folder_parts:
                folder_title = to_title(part)
                folder_path_key = f"{parent_path_key}/{folder_title}"

                if folder_path_key in existing_tree:
                    # reuse existing
                    parent_id = existing_tree[folder_path_key]['id']
                else:
                    # create folder page
                    page_info = get_or_create_page(folder_title, parent_id)
                    parent_id = page_info['id']
                    # add to existing_tree in-memory
                    existing_tree[folder_path_key] = {
                        'id': page_info['id'],
                        'title': page_info['title'],
                        'hash': page_info['hash'],
                        'parent_path': parent_path_key,
                        'parent_id': parent_id
                    }

                parent_path_key = folder_path_key

            # Now parent_path_key/parent_id represent this folder in Confluence
            # 2a. Handle files in this folder
            for filename in files:
                if not filename.endswith('.md'):
                    continue
                filepath = os.path.join(root, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    md_content = f.read()

                name_no_ext = os.path.splitext(filename)[0]
                if name_no_ext.lower() == 'index':
                    # index.md -> represents the folder page itself
                    page_title = to_title(folder_parts[-1]) if folder_parts else "Documentation Home"
                    page_path_key = f"{parent_path_key}/{page_title}"
                    is_folder_index = True
                else:
                    page_title = to_title(name_no_ext)
                    page_path_key = f"{parent_path_key}/{page_title}"
                    is_folder_index = False

                html_content = markdown.markdown(md_content)
                storage = f'<div class="markdown-body">{html_content}</div>'
                storage_hash = md5(storage)

                local_pages[page_path_key] = {
                    'title': page_title,
                    'storage': storage,
                    'hash': storage_hash,
                    'parent_path': parent_path_key,
                    'parent_id': parent_id,
                    'filepath': filepath,
                    'is_folder_index': is_folder_index
                }

    # 3. Decide creates/updates/deletes

    pages_to_create = []
    pages_to_update = []
    # Map path_key -> existing page for easier lookups
    # existing_tree keys look like 'ROOT/Docs/Guide/Part 1' and local_pages keys the same pattern.
    for path_key, local in local_pages.items():
        if path_key not in existing_tree:
            pages_to_create.append(local)
        else:
            remote = existing_tree[path_key]
            if local['hash'] != remote['hash']:
                pages_to_update.append({
                    'id': remote['id'],
                    'title': local['title'],
                    'storage': local['storage'],
                    'filepath': local['filepath'],
                    'parent_id': remote['parent_id'],
                    'path_key': path_key
                })
            else:
                print(f"Up to date: {path_key}")

    # Deletions: existing pages under ROOT that have no local counterpart
    pages_to_delete = []
    for path_key, remote in existing_tree.items():
        if path_key == 'ROOT':
            continue
        if path_key not in local_pages:
            # Only delete leaf pages: if any page has this as parent_path, skip delete
            is_parent = any(
                rp['parent_path'] == path_key
                for rp in existing_tree.values()
            )
            if not is_parent:
                pages_to_delete.append(remote)

    # 4. Apply changes

    # Creates
    for p in pages_to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']} from {p['filepath']}")
        try:
            confluence.create_page(
                space=CONFLUENCE_SPACE_KEY,
                parent_id=p['parent_id'],
                title=p['title'],
                body=p['storage'],
                representation='storage'
            )
        except Exception as e:
            print(f"Error creating page '{p['title']}': {e}")

    # Updates
    for p in pages_to_update:
        print(f"Updating page '{p['title']}' (ID {p['id']}) from {p['filepath']}")
        try:
            # need current version
            detail = confluence.get_page_by_id(p['id'], expand='version')
            current_ver = detail.get('version', {}).get('number', 1)
            confluence.update_page(
                page_id=p['id'],
                title=p['title'],
                body=p['storage'],
                representation='storage',
                version=current_ver + 1
            )
        except Exception as e:
            print(f"Error updating page '{p['title']}': {e}")

    # Deletes
    for p in pages_to_delete:
        print(f"Deleting page '{p['title']}' (ID {p['id']}) – no local markdown found")
        try:
            confluence.remove_page(page_id=p['id'], recursive=False)
        except Exception as e:
            print(f"Error deleting page '{p['title']}': {e}")

    print("Sync complete.")

if __name__ == "__main__":
    main()
