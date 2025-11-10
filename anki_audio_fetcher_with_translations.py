import os
import pandas as pd
import requests
import logging
import time
import random
from datetime import datetime
import re
from selenium import webdriver
from selenium.webdriver.common.by import By

from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup, NavigableString, Tag
from urllib.parse import quote_plus


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

def get_onelook_definition_selenium(word, delay_range=(2, 6)):
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1200,800")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)

    url = f"https://www.onelook.com/?w={word}&ls=a"
    try:
        driver.get(url)
        time.sleep(random.uniform(*delay_range))
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")

        # Find the in-brief definition box
        div = soup.find("div", class_="ol_inbrief")
        definition = ""
        #print(f" fetching OneLook definition for '{div}'")
 
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

    except Exception as e:
        print(f"Error fetching OneLook definition for '{word}': {e}")
        return None
    finally:
        driver.quit()

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

def find_working_audio_url(word):
    """Find a working audio URL for the given word"""
    if not word or pd.isna(word):
        return None, None
        
    candidates = construct_candidate_urls(word)
    for url in candidates:
        if try_url(url):
            return url, url.split('/')[-1]
    return None, None

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

def process_csv(input_csv, output_csv, audio_dir="audio", verbose=False):
    """Process CSV file to fetch audio and definitions"""
    try:
        # Read CSV with proper handling of empty values
        df = pd.read_csv(input_csv, keep_default_na=False, na_values=[''])
        
        # Ensure required columns exist
        required_columns = ['Front', 'Back', 'Audio', 'Definition', 'DL valid']
        for col in required_columns:
            if col not in df.columns:
                df[col] = ''
        
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
        if verbose:
            print(f"🔧 Debug mode enabled - will save HTML files and show detailed logs")
        
        successful_audio = 0
        successful_definitions = 0
        successful_onelook = 0
        
        for index, row in df.iterrows():
            word = str(row['Front']).strip()
            
            print(f"\n[{index+1}/{total_words}] Processing: '{word}'")
            
            # Check if Back column is empty and fetch OneLook definition
            current_back = row.get('Back', '')
            if is_empty_value(current_back):
                print(f"  Fetching OneLook definition...")
                if verbose:
                    print(f"  🔍 Debug mode: detailed OneLook analysis for '{word}'")
                
                #for word in words:
                onelook_def = get_onelook_definition_selenium(word)
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
            audio_url, filename = find_working_audio_url(word)
            
            # Construct definition URL
            definition_url = construct_definition_url(word)
            
            # Check if resources exist
            has_audio = audio_url is not None
            has_definition = check_definition_url(definition_url) if definition_url else False

            if not has_audio:
                logging.warning(f"Audio not found for word: {word}")
                print(f"  ✗ No audio found")
            else:
                print(f"  ✓ Audio found: {filename}")
                successful_audio += 1
                
            if not has_definition:
                logging.warning(f"Definition page not found for word: {word}")
                print(f"  ✗ Oxford definition page not found")
            else:
                print(f"  ✓ Oxford definition page found")
                successful_definitions += 1

            # Update DataFrame
            df.at[index, 'Audio'] = audio_url if has_audio else ""
            df.at[index, 'Definition'] = definition_url if has_definition else ""
            df.at[index, 'DL valid'] = has_audio

            # Download audio if found
            if has_audio:
                print(f"  Downloading audio...")
                if download_audio(audio_url, audio_dir):
                    print(f"  ✓ Audio downloaded successfully")
                    # Update Back column with Anki sound tag
                    current_back = str(df.at[index, 'Back']).strip()
                    if current_back and not is_empty_value(current_back):
                        # Add sound tag to existing content
                        df.at[index, 'Back'] = f"{current_back} [sound:{filename}]"
                    else:
                        df.at[index, 'Back'] = f"[sound:{filename}]"
                else:
                    print(f"  ✗ Audio download failed")
            
            # Add a delay between words to be respectful to servers
            # Longer delay after OneLook requests to avoid rate limiting
            if index < total_words - 1:  # Don't sleep after the last word
                base_delay = random.uniform(2.0, 4.0)  # Increased base delay
                # Add extra delay if we made OneLook requests
                if is_empty_value(current_back) or has_audio:
                    base_delay *= 1.5
                time.sleep(base_delay)

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

    parser = argparse.ArgumentParser(description="Fetch Oxford audio, definitions, and OneLook translations for word list.")
    parser.add_argument("input_csv", help="Input CSV file with 'Front' column")
    parser.add_argument("output_csv", help="Output CSV file to save results")
    parser.add_argument("--audio_dir", default="audio", help="Directory to save audio files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")

    args = parser.parse_args()
    process_csv(args.input_csv, args.output_csv, args.audio_dir, args.verbose)