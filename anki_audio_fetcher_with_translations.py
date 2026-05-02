import os
import json
import pandas as pd
import requests
import logging
import time
import random
import subprocess
from datetime import datetime
import re
from selenium import webdriver
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException
from selenium.webdriver.common.by import By

from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup, NavigableString, Tag
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse


# Setup logging
log_filename = "anki_audio_fetcher.log"
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filemode='a'  # Append to existing log file
)

BASE_URL = "https://www.oxfordlearnersdictionaries.com"
AUDIO_BASE = f"{BASE_URL}/media/english/us_pron_ogg"
ONELOOK_BASE = "https://onelook.com"
LOCAL_TTS_FALLBACK_MODEL = "/Users/alekseiplotnitskii/.lmstudio/models/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
LOCAL_TTS_RUNTIME_PYTHON = str(Path(__file__).resolve().parent / ".venv-tts" / "bin" / "python")
LOCAL_TTS_HELPER_SCRIPT = str(Path(__file__).resolve().parent / "qwen_tts_fallback.py")
LOCAL_TTS_OUTPUT_FORMAT = "ogg"

def create_selenium_driver():
    """Create a single headless Chrome driver for OneLook scraping."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1200,800")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    try:
        return webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    except Exception as e:
        logging.error(f"Failed to create Selenium driver: {e}")
        return None

def restart_selenium_driver(driver):
    """Restart the shared Selenium driver after a browser/session failure."""
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
    return create_selenium_driver()

def get_onelook_definition_selenium(word, driver, delay_range=(2, 6)):
    """Fetch OneLook definition using a shared Selenium driver."""
    if driver is None:
        return None

    url = f"https://www.onelook.com/?w={word}&ls=a"
    try:
        driver.get(url)
        time.sleep(random.uniform(*delay_range))
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Find the in-brief definition box
        div = soup.find("div", class_="ol_inbrief")
        definition = ""
 
        if div:
            # Find the span with "Usually means:"
            span = div.find("span", class_="ol_inbrief_title")
            if span:
                prev_ended_with_space = True  # Track spacing between nodes
                for sib in span.next_siblings:
                    if isinstance(sib, NavigableString):
                        text = sib.strip()
                        if text:
                            if not prev_ended_with_space:
                                definition += " "
                            definition += text
                            prev_ended_with_space = text.endswith(" ")
                    elif isinstance(sib, Tag):
                        tag_text = sib.get_text(strip=True)
                        if definition and not definition.endswith(" "):
                            definition += " "
                        definition += tag_text
                        prev_ended_with_space = tag_text.endswith(" ")
            definition = definition.strip()

        
        if not definition:
            definition = "Definition not found."

        return definition

    except (InvalidSessionIdException, WebDriverException) as e:
        message = str(e).lower()
        if any(marker in message for marker in ("invalid session id", "session deleted", "not connected to devtools")):
            logging.error(f"Selenium session lost while fetching OneLook definition for '{word}': {e}")
            raise
        print(f"Error fetching OneLook definition for '{word}': {e}")
        return None
    except Exception as e:
        print(f"Error fetching OneLook definition for '{word}': {e}")
        return None

def get_human_headers():
    """Generate realistic browser headers"""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15'
    ]
    
    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

def safe_segment(word, length):
    """Safely create URL segments with proper padding"""
    if not word:
        return '_' * length
    return word[:length].ljust(length, '_')

def try_url(url):
    """Test if a URL is accessible"""
    headers = get_human_headers()
    try:
        r = requests.get(url, stream=True, timeout=10, headers=headers)
        return r.status_code == 200
    except Exception as e:
        logging.debug(f"URL test failed for {url}: {e}")
        return False

def clean_word_for_url(word):
    """Clean word for URL construction"""
    if not word or pd.isna(word):
        return ""
    
    word = str(word).strip().lower()
    # Replace spaces and hyphens with underscores
    word = re.sub(r'[\s\-]+', '_', word)
    # Remove special characters except underscores
    word = re.sub(r'[^\w_]', '', word)
    return word

def construct_candidate_urls(word):
    """Construct potential audio URLs for a word"""
    word_clean = clean_word_for_url(word)
    if not word_clean:
        return []
        
    paths = []
    
    try:
        # Basic URL components
        part1 = word_clean[0] if word_clean else 'a'
        part2 = safe_segment(word_clean, 3)
        part3 = safe_segment(word_clean, 5)
        
        # 1. US PRON OGG (.ogg)
        filename = f"{word_clean}__us_1.ogg"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron_ogg/{part1}/{part2}/{part3}/{filename}")

        # 2. US PRON (.mp3)
        filename_mp3 = f"{word_clean}__us_1.mp3"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron/{part1}/{part2}/{part3}/{filename_mp3}")

        # 3. US PRON RR (.mp3, regional/ranked/rare)
        filename_mp3_rr = f"{word_clean}__us_1_rr.mp3"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron/{part1}/{part2}/{part3}/{filename_mp3_rr}")

        # 4. US PRON OGG RR (.ogg)
        filename_ogg_rr = f"{word_clean}__us_1_rr.ogg"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron_ogg/{part1}/{part2}/{part3}/{filename_ogg_rr}")

        # 5. US PRON Numbered (.mp3)
        for n in range(1, 4):
            filename_n = f"{word_clean}__us_{n}.mp3"
            paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron/{part1}/{part2}/{part3}/{filename_n}")

        # 6. UK PRON (.mp3)
        filename_gb = f"{word_clean}__gb_1.mp3"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/{part1}/{part2}/{part3}/{filename_gb}")

        # 7. "x" prefixes (for derived/compound words)
        xpart2 = 'x' + part2
        xpart3 = 'x' + part3
        xfilename = f"x{word_clean}__us_1.mp3"
        paths.append(f"https://www.oxfordlearnersdictionaries.com/media/english/us_pron/x/{xpart2}/{xpart3}/{xfilename}")
        
    except Exception as e:
        logging.error(f"Error constructing URLs for word '{word}': {e}")
    
    return paths

def extract_audio_urls_from_oxford_html(html):
    """Extract candidate US audio URLs from Oxford HTML responses."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen = set()

    for node in soup.select(".sound.audio_play_button.pron-us"):
        for attr in ("data-src-ogg", "data-src-mp3"):
            url = node.get(attr)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

    if urls:
        return urls

    pattern = r'https://www\.oxfordlearnersdictionaries\.com/media/english/(?:us_pron_ogg|us_pron)/[^"\'\s<>]+'
    for url in re.findall(pattern, html):
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls

def normalize_lookup_text(text):
    """Normalize text for loose matching across filenames, slugs, and queries."""
    if not text:
        return ""

    text = str(text).lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=\D)\d+$", "", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def extract_audio_stem(audio_url):
    """Extract the lexical stem from an Oxford audio filename."""
    filename = audio_url.split("/")[-1].rsplit(".", 1)[0]
    filename = re.sub(r"__(?:us|gb)_\d+(?:_rr)?$", "", filename)
    if filename.startswith("x") and len(filename) > 1:
        filename = filename[1:]
    return filename

def is_relevant_definition_url(url, word):
    """Check whether a resolved Oxford entry is a close match for the queried word."""
    if not is_definition_entry_url(url):
        return False

    slug_norm = normalize_lookup_text(urlparse(url).path.rsplit("/", 1)[-1])
    word_norm = normalize_lookup_text(word)
    if not slug_norm or not word_norm:
        return False

    if slug_norm == word_norm or slug_norm.startswith(f"{word_norm} "):
        return True

    slug_compact = slug_norm.replace(" ", "")
    word_compact = word_norm.replace(" ", "")

    if slug_compact == word_compact:
        return True
    if word_compact.endswith("s") and slug_compact == word_compact[:-1]:
        return True
    if word_compact.endswith("or") and slug_compact == f"{word_compact[:-2]}our":
        return True
    if word_compact.endswith("our") and slug_compact == f"{word_compact[:-3]}or":
        return True

    return False

def is_relevant_audio_url(audio_url, word, page_url=None):
    """Reject unrelated audio widgets that can appear on some Oxford pages."""
    audio_norm = normalize_lookup_text(extract_audio_stem(audio_url))
    if not audio_norm:
        return False

    use_page_slug = bool(page_url and is_relevant_definition_url(page_url, word))
    if use_page_slug:
        comparison_values = [normalize_lookup_text(urlparse(page_url).path.rsplit("/", 1)[-1])]
    else:
        comparison_values = [normalize_lookup_text(word)]

    for candidate in comparison_values:
        if not candidate:
            continue
        if audio_norm == candidate:
            return True
        if use_page_slug and audio_norm.startswith(f"{candidate} "):
            return True

        if use_page_slug:
            audio_tokens = set(audio_norm.split())
            candidate_tokens = set(candidate.split())
            if candidate_tokens and candidate_tokens.issubset(audio_tokens):
                return True

        audio_compact = audio_norm.replace(" ", "")
        candidate_compact = candidate.replace(" ", "")

        if candidate_compact == audio_compact:
            return True
        if candidate_compact.endswith("s") and audio_compact == candidate_compact[:-1]:
            return True
        if candidate_compact.endswith("or") and audio_compact == f"{candidate_compact[:-2]}our":
            return True
        if candidate_compact.endswith("our") and audio_compact == f"{candidate_compact[:-3]}or":
            return True

    return False

def is_definition_entry_url(url):
    """Check whether a URL points to an Oxford definition entry page."""
    if not url:
        return False

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return path.startswith("/definition/english/") and path != "/definition/english"

def extract_definition_entry_links(html, base_url):
    """Extract Oxford definition entry links from a response page."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()

    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, anchor.get("href", "").strip())
        if not is_definition_entry_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)

    return links

def find_audio_on_oxford_pages(word, max_link_follows=8):
    """Find audio by parsing Oxford definition/search pages and linked entries."""
    if not word or pd.isna(word):
        return None, None, None

    word_clean = clean_word_for_url(word)
    query = quote_plus(str(word).strip())
    page_urls = []

    if word_clean:
        page_urls.append(f"{BASE_URL}/definition/english/{word_clean}?q={query}")
    page_urls.append(f"{BASE_URL}/search/english/?q={query}")

    visited = set()
    queue = list(page_urls)
    followed_links = 0

    while queue:
        page_url = queue.pop(0)
        if page_url in visited:
            continue
        visited.add(page_url)

        try:
            headers = get_human_headers()
            response = requests.get(page_url, timeout=10, headers=headers)
        except Exception as e:
            logging.debug(f"Oxford page lookup failed for {page_url}: {e}")
            continue

        for audio_url in extract_audio_urls_from_oxford_html(response.text):
            if not is_relevant_audio_url(audio_url, word, response.url):
                continue
            if try_url(audio_url):
                resolved_definition_url = response.url if is_definition_entry_url(response.url) else None
                return audio_url, audio_url.split("/")[-1], resolved_definition_url

        # If Oxford already resolved to a concrete entry page and it still doesn't expose
        # relevant audio for this query, don't wander into related-entry links.
        if is_definition_entry_url(response.url):
            continue

        if followed_links >= max_link_follows:
            continue

        for linked_page in extract_definition_entry_links(response.text, response.url):
            if linked_page in visited or linked_page in queue:
                continue
            queue.append(linked_page)
            followed_links += 1
            if followed_links >= max_link_follows:
                break

    return None, None, None

def find_working_audio_url(word):
    """Find a working audio URL for the given word"""
    if not word or pd.isna(word):
        return None, None, None
        
    candidates = construct_candidate_urls(word)
    for url in candidates:
        if try_url(url):
            return url, url.split('/')[-1], None

    return find_audio_on_oxford_pages(word)

def generate_tts_fallback_audio(word, output_folder):
    """Generate fallback audio locally via the dedicated Python 3.11 TTS runtime."""
    if not word or pd.isna(word):
        return None, None

    runtime_python = Path(LOCAL_TTS_RUNTIME_PYTHON)
    helper_script = Path(LOCAL_TTS_HELPER_SCRIPT)
    if not runtime_python.exists():
        logging.error(f"TTS runtime python not found: {runtime_python}")
        return None, None
    if not helper_script.exists():
        logging.error(f"TTS helper script not found: {helper_script}")
        return None, None

    output_dir = Path(output_folder)
    output_dir.mkdir(parents=True, exist_ok=True)

    file_prefix = f"{clean_word_for_url(word)}__tts"
    output_path = output_dir / f"{file_prefix}.{LOCAL_TTS_OUTPUT_FORMAT}"
    relative_audio_path = str(output_path)
    if output_path.exists():
        return relative_audio_path, output_path.name

    command = [
        str(runtime_python),
        str(helper_script),
        "--model-path",
        LOCAL_TTS_FALLBACK_MODEL,
        "--text",
        str(word).strip(),
        "--output-dir",
        str(output_dir),
        "--file-prefix",
        file_prefix,
        "--output-format",
        LOCAL_TTS_OUTPUT_FORMAT,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except Exception as e:
        logging.error(f"TTS fallback execution failed for '{word}': {e}")
        return None, None

    marker = "TTS_RESULT_JSON="
    combined_output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    payload = None
    for line in combined_output.splitlines():
        if line.startswith(marker):
            try:
                payload = json.loads(line[len(marker):])
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse TTS fallback payload for '{word}': {e}")
            break

    if result.returncode != 0:
        logging.error(
            f"TTS fallback process failed for '{word}' with code {result.returncode}: {combined_output[-2000:]}"
        )
        return None, None

    if not payload:
        logging.error(f"TTS fallback produced no result payload for '{word}': {combined_output[-2000:]}")
        return None, None

    generated_path = payload.get("output_path")
    generated_filename = payload.get("filename")
    if not generated_path or not generated_filename:
        logging.error(f"TTS fallback returned incomplete payload for '{word}': {payload}")
        return None, None

    generated_file = Path(generated_path)
    if not generated_file.exists():
        logging.error(f"TTS fallback output file missing for '{word}': {generated_file}")
        return None, None

    return str(output_dir / generated_filename), generated_filename

def is_local_audio_file(audio_reference, audio_dir):
    """Check whether the audio reference points to an existing local file in audio_dir."""
    if not audio_reference:
        return False

    try:
        audio_path = Path(audio_reference).expanduser().resolve()
        base_dir = Path(audio_dir).expanduser().resolve()
        return audio_path.exists() and audio_path.is_file() and base_dir in audio_path.parents
    except Exception:
        return False

def construct_definition_url(word):
    """Construct Oxford definition URL"""
    if not word or pd.isna(word):
        return ""
    word_clean = clean_word_for_url(word)
    return f"{BASE_URL}/definition/english/{word_clean}"

def check_definition_url(url):
    """Check if definition URL is accessible"""
    if not url:
        return False
    try:
        headers = get_human_headers()
        r = requests.get(url, timeout=10, headers=headers)
        return r.status_code == 200
    except Exception as e:
        logging.error(f"Error checking definition URL: {url} | {e}")
        return False


def download_audio(url, output_folder):
    """Download audio file from URL"""
    if not url:
        return False
        
    try:
        os.makedirs(output_folder, exist_ok=True)
        filename = url.split("/")[-1]
        filepath = os.path.join(output_folder, filename)

        headers = get_human_headers()

        r = requests.get(url, stream=True, timeout=15, headers=headers)
        if r.status_code == 200:
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            return True
        else:
            logging.error(f"Download failed, status code {r.status_code}: {url}")
            return False
    except Exception as e:
        logging.error(f"Failed to download audio from {url} | {e}")
        return False

def is_empty_value(value):
    """Check if a value is empty (None, NaN, empty string, or whitespace)"""
    if pd.isna(value):
        return True
    if value is None:
        return True
    if str(value).strip() == '':
        return True
    if str(value).lower() in ['nan', 'none', 'null']:
        return True
    return False

def append_sound_tag(back_value, filename):
    """Append an Anki sound tag without duplicating it on repeated runs."""
    sound_tag = f"[sound:{filename}]"
    if is_empty_value(back_value):
        return sound_tag

    back_text = str(back_value).strip()
    if sound_tag in back_text:
        return back_text
    return f"{back_text} {sound_tag}"

def process_csv(input_csv, output_csv, audio_dir="audio", verbose=False, tts_only=False):
    """Process CSV file to fetch audio and definitions"""
    try:
        # Read CSV with proper handling of empty values
        df = pd.read_csv(input_csv, keep_default_na=False, na_values=[''])
        
        # Ensure required columns exist
        required_columns = ['Front', 'Back', 'Audio', 'Definition', 'DL valid']
        for col in required_columns:
            if col not in df.columns:
                df[col] = ''
            df[col] = df[col].astype('object')
        
        # Filter out empty Front values
        original_count = len(df)
        df = df[df['Front'].notna() & (df['Front'].astype(str).str.strip() != '')]
        filtered_count = len(df)
        
        if filtered_count < original_count:
            print(f"⚠️  Filtered out {original_count - filtered_count} rows with empty 'Front' values")
        
        total_words = len(df)
        
        if total_words == 0:
            print("❌ No valid words found in the CSV file!")
            return
        
        print(f"Processing {total_words} words...")
        if tts_only:
            print("TTS-only mode enabled: skipping OneLook, Oxford lookup, and downloads.")
        if verbose:
            print(f"🔧 Debug mode enabled - will save HTML files and show detailed logs")
        
        successful_audio = 0
        successful_definitions = 0
        successful_onelook = 0
        driver = None if tts_only else create_selenium_driver()
        if not tts_only and driver is None:
            print("⚠️  Unable to start Selenium; OneLook definitions will be skipped.")
        
        try:
            for index, row in df.iterrows():
                word = str(row['Front']).strip()
                
                print(f"\n[{index+1}/{total_words}] Processing: '{word}'")

                if tts_only:
                    print(f"  Generating local TTS audio...")
                    tts_audio_path, tts_filename = generate_tts_fallback_audio(word, audio_dir)

                    if tts_audio_path and tts_filename and is_local_audio_file(tts_audio_path, audio_dir):
                        successful_audio += 1
                        df.at[index, 'Back'] = append_sound_tag(row.get('Back', ''), tts_filename)
                        df.at[index, 'Audio'] = tts_audio_path
                        df.at[index, 'DL valid'] = True
                        print(f"  ✓ TTS audio ready: {tts_filename}")
                    else:
                        df.at[index, 'Audio'] = ""
                        df.at[index, 'DL valid'] = False
                        print(f"  ✗ TTS generation failed")

                    continue
                
                # Check if Back column is empty and fetch OneLook definition
                current_back = row.get('Back', '')
                if is_empty_value(current_back):
                    print(f"  Fetching OneLook definition...")
                    if verbose:
                        print(f"  🔍 Debug mode: detailed OneLook analysis for '{word}'")

                    try:
                        onelook_def = get_onelook_definition_selenium(word, driver)
                    except (InvalidSessionIdException, WebDriverException):
                        print(f"  ⚠️ Selenium session dropped, restarting browser and retrying...")
                        logging.warning(f"Selenium session dropped while fetching '{word}'. Restarting driver.")
                        driver = restart_selenium_driver(driver)
                        if driver is None:
                            print(f"  ⚠️ Unable to restart Selenium; skipping OneLook definition")
                            onelook_def = None
                        else:
                            try:
                                onelook_def = get_onelook_definition_selenium(word, driver)
                            except (InvalidSessionIdException, WebDriverException) as retry_error:
                                logging.error(f"Selenium retry failed for '{word}': {retry_error}")
                                print(f"  ⚠️ Selenium retry failed; skipping OneLook definition")
                                driver = None
                                onelook_def = None

                    print(f"{word}: {onelook_def}")


                    if onelook_def:
                        df.at[index, 'Back'] = onelook_def
                        successful_onelook += 1
                        print(f"  ✓ Definition: {onelook_def[:100]}{'...' if len(onelook_def) > 100 else ''}")
                    else:
                        print(f"  ✗ No definition found")
                else:
                    print(f"  ↳ Back column already has content, skipping definition fetch")
                
                # Find working audio URL
                print(f"  Searching for audio...")
                audio_url, filename, resolved_definition_url = find_working_audio_url(word)
                
                # Construct definition URL
                definition_url = resolved_definition_url or construct_definition_url(word)
                
                # Check if resources exist
                has_audio = audio_url is not None
                has_definition = bool(resolved_definition_url) or (check_definition_url(definition_url) if definition_url else False)

                if not has_audio:
                    logging.warning(f"Audio not found for word: {word}")
                    print(f"  ✗ No audio found")
                    print(f"  Attempting local TTS fallback...")
                    tts_audio_path, tts_filename = generate_tts_fallback_audio(word, audio_dir)
                    if tts_audio_path and tts_filename:
                        audio_url = tts_audio_path
                        filename = tts_filename
                        has_audio = True
                        successful_audio += 1
                        print(f"  ✓ TTS audio generated: {filename}")
                    else:
                        print(f"  ✗ TTS fallback unavailable")
                else:
                    print(f"  ✓ Audio found: {filename}")
                    successful_audio += 1
                    
                if not has_definition:
                    logging.warning(f"Definition page not found for word: {word}")
                    print(f"  ✗ Oxford definition page not found")
                else:
                    print(f"  ✓ Oxford definition page found")
                    successful_definitions += 1

                # Track download success so DL valid and sound tags only reflect completed downloads
                download_success = False

                # Download audio if found
                if has_audio:
                    if is_local_audio_file(audio_url, audio_dir):
                        download_success = True
                        print(f"  ✓ Audio already generated locally")
                    else:
                        print(f"  Downloading audio...")
                    if download_success or download_audio(audio_url, audio_dir):
                        download_success = True
                        if not is_local_audio_file(audio_url, audio_dir):
                            print(f"  ✓ Audio downloaded successfully")
                        # Update Back column with Anki sound tag
                        df.at[index, 'Back'] = append_sound_tag(df.at[index, 'Back'], filename)
                    else:
                        print(f"  ✗ Audio download failed")

                # Update DataFrame after attempting download
                df.at[index, 'Audio'] = audio_url if has_audio else ""
                df.at[index, 'Definition'] = definition_url if has_definition else ""
                df.at[index, 'DL valid'] = download_success

                # Add a delay between words to be respectful to servers
                # Longer delay after OneLook requests to avoid rate limiting
                if index < total_words - 1:  # Don't sleep after the last word
                    base_delay = random.uniform(2.0, 4.0)  # Increased base delay
                    # Add extra delay if we made OneLook requests
                    if is_empty_value(current_back) or has_audio:
                        base_delay *= 1.5
                    time.sleep(base_delay)
        finally:
            if driver:
                driver.quit()

        # Save results
        df.to_csv(output_csv, index=False)
        print(f"\n🎉 Processing complete!")
        print(f"Results saved to: '{output_csv}'")
        print(f"Audio files saved to: '{audio_dir}' directory")
        print(f"Log saved to: '{log_filename}'")
        
        # Summary statistics
        print(f"\n📊 Summary:")
        print(f"  Total words processed: {total_words}")
        print(f"  Audio files found: {successful_audio}")
        if not tts_only:
            print(f"  Oxford definition pages found: {successful_definitions}")
            print(f"  OneLook definitions added: {successful_onelook}")
        
    except FileNotFoundError:
        print(f"❌ Error: Input file '{input_csv}' not found!")
        logging.error(f"Input file not found: {input_csv}")
    except pd.errors.EmptyDataError:
        print(f"❌ Error: Input file '{input_csv}' is empty!")
        logging.error(f"Input file is empty: {input_csv}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        logging.error(f"Unexpected error in process_csv: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch audio and definitions for Anki word cards.")
    parser.add_argument("input_csv", help="Input CSV file with 'Front' column")
    parser.add_argument("output_csv", help="Output CSV file to save results")
    parser.add_argument("--audio_dir", default="audio", help="Directory to save audio files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--tts-only",
        action="store_true",
        help="Generate local TTS audio only; skip OneLook definitions and Oxford lookups",
    )

    args = parser.parse_args()
    process_csv(args.input_csv, args.output_csv, args.audio_dir, args.verbose, args.tts_only)
