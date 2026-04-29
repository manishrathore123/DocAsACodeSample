import os
import sys
import hashlib
import re
import json
import requests
from datetime import datetime
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')
CONFLUENCE_ARCHIVE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_ARCHIVE_PARENT_PAGE_ID')

# Optional Mermaid syntax type: "codeblock" or "mermaid"
CONFLUENCE_MERMAID_SYNTAX = os.environ.get('CONFLUENCE_MERMAID_SYNTAX', 'mermaid')

DOCS_FOLDER = "docs"
ARCHIVE_FOLDER_TITLE = "Archive"

# Initialize Confluence instance
try:
    confluence = Confluence(
        url=CONFLUENCE_URL,
        username=CONFLUENCE_USERNAME,
        password=CONFLUENCE_API_TOKEN,
        cloud=True,
    )
except Exception as e:
    print(f"FATAL: Error connecting to Confluence: {e}")
    sys.exit(1)

def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def to_title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()

def confluence_api_request(method, url, data):
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {confluence._session.auth[1]}',
    }
    response = requests.request(method, url, headers=headers, data=json.dumps(data))
    if not response.ok:
        raise Exception(f"Confluence API {method} failed: {response.status_code} {response.text}")
    return response.json()

def create_page_direct(space, parent_id, title, storage_body):
    url = f"{CONFLUENCE_URL}/wiki/rest/api/content/"
    data = {
        "type": "page",
        "title": title,
        "space": {"key": space},
        "ancestors": [{"id": parent_id}],
        "body": {"storage": {"value": storage_body, "representation": "storage"}},
    }
    return confluence_api_request("POST", url, data)

def get_page_version(page_id):
    url = f"{CONFLUENCE_URL}/wiki/rest/api/content/{page_id}?expand=version"
    headers = {
        'Authorization': f'Basic {confluence._session.auth[1]}',
        'Content-Type': 'application/json',
    }
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()
    return data['version']['number']

def update_page_direct(page_id, title, storage_body, version):
    url = f"{CONFLUENCE_URL}/wiki/rest/api/content/{page_id}"
    data = {
        "version": {"number": version + 1},
        "title": title,
        "type": "page",
        "body": {"storage": {"value": storage_body, "representation": "storage"}},
    }
    return confluence_api_request("PUT", url, data)

def move_page_to_parent_direct(page_id, new_parent_id, version):
    url = f"{CONFLUENCE_URL}/wiki/rest/api/content/{page_id}"
    data = {
        "version": {"number": version + 1},
        "type": "page",
        "ancestors": [{"id": new_parent_id}],
    }
    return confluence_api_request("PUT", url, data)

def markdown_to_storage(md_content: str) -> str:
    mermaid_pattern = re.compile(r"(```mermaid\n.*?\n```)", re.DOTALL)
    parts = mermaid_pattern.split(md_content)

    final_html_parts = []

    for part in parts:
        if part.startswith("```mermaid"):
            inner_content_match = re.search(r"```mermaid\n(.*?)\n```", part, re.DOTALL)
            if inner_content_match:
                mermaid_code = inner_content_match.group(1).strip().replace(u'\xa0', u' ')
                if CONFLUENCE_MERMAID_SYNTAX == 'codeblock':
                    macro = (
                        f'<ac:structured-macro ac:name="code">'
                        f'<ac:parameter ac:name="language">mermaid</ac:parameter>'
                        f'<ac:plain-text-body><![CDATA[{mermaid_code}]]></ac:plain-text-body>'
                        f'</ac:structured-macro>'
                    )
                else:
                    macro = (
                        f'<ac:structured-macro ac:name="mermaid">'
                        f'<ac:plain-text-body><![CDATA[{mermaid_code}]]></ac:plain-text-body>'
                        f'</ac:structured-macro>'
                    )
                final_html_parts.append(macro)
        elif part.strip():
            html_part = markdown.markdown(part, extensions=['fenced_code', 'tables'])
            final_html_parts.append(html_part)

    return "".join(final_html_parts)

def find_page_in_space_by_title(title: str):
    try:
        return confluence.get_page_by_title(
            space=CONFLUENCE_SPACE_KEY,
            title=title,
            expand='ancestors,body.storage,version'
        )
    except Exception:
        return None

def ensure_folder_page(folder_title: str, parent_id: str) -> str:
    try:
        cql = f'title = "{folder_title}" AND ancestor = {parent_id} AND type = page'
        res = confluence.cql(cql, limit=1, expand='content.id')
        if res and res.get('results'):
            return res['results'][0]['content']['id']
    except Exception as e:
        print(f"Info: CQL query for folder '{folder_title}' failed, error {e}")

    existing = find_page_in_space_by_title(folder_title)
    if existing:
        try:
            ancestors = existing.get('ancestors') or []
            if ancestors and str(ancestors[-1].get('id')) == str(parent_id):
                return existing['id']
            print(f"Warning: Folder '{folder_title}' found but in wrong parent, creating new page.")
        except Exception:
            pass

    try:
        created_page = create_page_direct(CONFLUENCE_SPACE_KEY, parent_id, folder_title, "")
        if created_page and created_page.get('id'):
            return created_page['id']
    except Exception as e:
        print(f"Error creating folder page '{folder_title}': {e}")

    try:
        res = confluence.cql(cql, limit=1, expand='content.id')
        if res and res.get('results'):
            return res['results'][0]['content']['id']
    except Exception:
        pass

    raise RuntimeError(f"Unable to create or find folder page '{folder_title}'")

def ensure_archive_parent() -> str:
    if CONFLUENCE_ARCHIVE_PARENT_PAGE_ID:
        try:
            page = confluence.get_page_by_id(CONFLUENCE_ARCHIVE_PARENT_PAGE_ID, expand='id')
            if page and page.get('id'):
                return page['id']
        except Exception:
            print("Warning: Archive parent page ID not found, creating default.")

    return ensure_folder_page(ARCHIVE_FOLDER_TITLE, CONFLUENCE_PARENT_PAGE_ID)

def main():
    print("Starting Confluence Markdown sync...")
    
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Missing required environment variables. Aborting.")
        sys.exit(1)

    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, _ in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
            for d in sorted(dirs):
                sub_folder_path = os.path.join(folder_path, d).replace("\\", "/")
                folder_title = to_title(d)
                folder_parent_ids[sub_folder_path] = ensure_folder_page(folder_title, parent_id)
    else:
        print(f"Warning: Docs folder '{DOCS_FOLDER}' not found.")

    local_pages = {}
    for root, _, files in os.walk(DOCS_FOLDER):
        rel = os.path.relpath(root, DOCS_FOLDER)
        folder_path = "" if rel == "." else rel.replace("\\", "/")
        parent_id = folder_parent_ids[folder_path]
        for filename in sorted(files):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(root, filename)
            with open(filepath, encoding='utf-8') as f:
                md_content = f.read()
            name_no_ext = os.path.splitext(filename)[0]
            title = to_title(os.path.basename(folder_path)) if name_no_ext.lower() == "index" and folder_path else to_title(name_no_ext)

            storage = markdown_to_storage(md_content)
            content_hash = md5(storage)
            local_pages[(parent_id, title)] = {
                "title": title,
                "storage": storage,
                "hash": content_hash,
                "parent_id": parent_id,
                "filepath": filepath
            }

    confluence_pages = {}
    pages_from_space = confluence.get_all_pages_from_space(CONFLUENCE_SPACE_KEY, expand='ancestors,body.storage,version')
    for page in pages_from_space:
        p_id = page['id']
        p_title = page['title']
        p_parent_id = page['ancestors'][-1]['id'] if page.get('ancestors') else None
        storage_val = page.get('body', {}).get('storage', {}).get('value', '')
        confluence_pages[(p_parent_id, p_title)] = {
            "id": p_id,
            "hash": md5(storage_val),
            "version": page['version']['number'],
        }

    to_create = []
    to_update = []
    processed_keys = set()

    for key, local_page in local_pages.items():
        processed_keys.add(key)
        remote_page = confluence_pages.get(key)
        if not remote_page:
            to_create.append(local_page)
        elif local_page['hash'] != remote_page['hash']:
            merged = local_page.copy()
            merged.update(remote_page)
            to_update.append(merged)

    archive_parent_id = ensure_archive_parent()
    managed_folder_ids = set(folder_parent_ids.values())

    to_archive = []
    for key, remote_page in confluence_pages.items():
        rid = str(remote_page.get('id'))
        if (key not in processed_keys and rid not in managed_folder_ids and rid != str(archive_parent_id) and rid != str(CONFLUENCE_PARENT_PAGE_ID)):
            to_archive.append({**remote_page, "title": key[1], "parent_id": key[0]})

    print(f"Creating {len(to_create)} pages...")
    for page in to_create:
        try:
            create_page_direct(page['parent_id'], page['parent_id'], page['title'], page['storage'])
            print(f"Created '{page['title']}'")
        except Exception as e:
            print(f"Failed to create '{page['title']}': {e}")

    print(f"Updating {len(to_update)} pages...")
    for page in to_update:
        try:
            update_page_direct(page['id'], page['title'], page['storage'], page['version'])
            print(f"Updated '{page['title']}'")
        except Exception as e:
            print(f"Failed to update '{page['title']}': {e}")

    print(f"Archiving {len(to_archive)} pages...")
    for page in to_archive:
        try:
            current_version = get_page_version(page['id'])
            move_page_to_parent_direct(page['id'], archive_parent_id, current_version)
            print(f"Archived '{page['title']}'")
        except Exception as e:
            print(f"Failed to archive '{page['title']}': {e}")

    print("Sync complete.")

if __name__ == "__main__":
    main()
