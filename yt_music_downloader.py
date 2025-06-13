"""
pip install streamlit selenium webdriver-manager pytube　yt_dlp
python -m streamlit run yt_music_downloader.py
"""

import streamlit as st
import os
import time
from concurrent.futures import ThreadPoolExecutor
import re

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- Configuration & Helper Functions ---
DOWNLOAD_PATH = 'music/'
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

def sanitize_filename(name):
    name = str(name).strip()
    # Replace characters that are invalid in Windows filenames and also commonly problematic
    name = re.sub(r'[\\/*?:"<>|]', "_", name) 
    # Replace multiple spaces with a single underscore
    name = re.sub(r'\s+', '_', name) 
    # Optionally, limit filename length (e.g., to 100 characters)
    # name = name[:100] if len(name) > 100 else name
    if not name: # If name becomes empty after sanitization
        return "_untitled_"
    return name

@st.cache_resource # Cache the driver for the session to avoid re-initializing
def get_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless') # Run browser in the background
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    # Set language to English for potentially more stable selectors, though YouTube Music is good with multilingual
    options.add_argument("lang=en-US") 
    options.add_experimental_option('prefs', {'intl.accept_languages': 'en,en_US'})
    
    # Use webdriver-manager to automatically handle ChromeDriver
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=options)
    return driver

def check_ffmpeg_available():
    import subprocess
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        if result.returncode == 0:
            return True
    except FileNotFoundError:
        pass
    return False

def close_driver():
    # Function to properly close the Selenium WebDriver
    if 'driver' in st.session_state and st.session_state.driver:
        st.session_state.driver.quit()
        del st.session_state.driver # Remove from session state

# --- Selenium Search Logic ---
def search_yt_music_songs(driver, query, search_type, max_songs=20):
    st.write(f"正在 YouTube Music 上搜尋 '{query}' (類型: {search_type})...")
    base_url = "https://music.youtube.com/"
    
    # Construct search URL using the query
    search_query_url_part = f"search?q={query.replace(' ', '+')}"
    full_search_url = f"{base_url}{search_query_url_part}"
    
    driver.get(full_search_url)
    
    # Wait for initial results to load
    try:
        WebDriverWait(driver, 60).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ytmusic-responsive-list-item-renderer"))
        )
    except Exception as e:
        st.warning(f"載入搜尋結果時發生錯誤或超時。可能是網路問題或 YouTube Music 頁面結構已更改。詳細資訊: {e}")
        return []
    
    # 找到音樂清單
    try:
        h2_elements = driver.find_elements(By.CSS_SELECTOR, "h2.title.style-scope.ytmusic-shelf-renderer")

        target_shelf = None
        for h2 in h2_elements:
            try:
                text = h2.find_element(By.TAG_NAME, "yt-formatted-string").text.strip()
                if text == "Songs":
                    # 找到外層 <ytmusic-shelf-renderer>
                    target_shelf = h2.find_element(By.XPATH, "./ancestor::ytmusic-shelf-renderer")
                    break
            except Exception:
                continue
    except Exception as e:
        print(str(e), "失敗啦")
    buttons = target_shelf.find_elements(By.TAG_NAME, "button")
    target_button = None
    for btn in buttons:
        if "Show all" in btn.text:
            target_button = btn
            break

    if target_button:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", target_button)
        time.sleep(1)
        target_button.click()
        print("✅ 成功點擊歌曲區塊的『顯示全部』按鈕")
    else:
        print("❌ 歌曲區塊找不到『顯示全部』按鈕")
    time.sleep(3)
    songs_data = []
    seen_urls = set() # To avoid duplicate entries

    # Scroll down multiple times to load more song results
    num_scrolls = 7 # Adjust based on how many results are typically needed
    for i in range(num_scrolls):
        if len(songs_data) >= max_songs:
            break
        driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
        time.sleep(1.5) # Allow time for new content to load after scroll

        # Selectors for song elements (these are critical and might change if YouTube Music updates its HTML structure)
        # song_elements = driver.find_elements(By.CSS_SELECTOR, "style-scope ytmusic-section-list-renderer")
        song_elements = driver.find_element(By.CSS_SELECTOR, "div#contents.style-scope.ytmusic-section-list-renderer")
        a_tags = song_elements.find_elements(By.CSS_SELECTOR, "a.yt-simple-endpoint.style-scope.yt-formatted-string")
        for i, a in enumerate(a_tags, start=1):
            text = a.text.strip()
            href = a.get_attribute("href")
            if 'watch' in href:
                href = href.replace("https://music.", "https://www.")
                if not href in seen_urls:
                    songs_data.append({
                        'title': text,
                        'url': href,
                        'id': href # Use URL as a unique ID for selection
                    })
                    seen_urls.add(href)

        st.write(f"已捲動 {i+1}/{num_scrolls} 次，目前找到 {len(songs_data)} 首歌曲...")
        if len(songs_data) >= max_songs:
            break
    
    st.write(f"搜尋完畢，總共找到 {len(songs_data)} 首相關歌曲。")
    return songs_data[:max_songs] # Return up to max_songs


# --- Pytube Download Logic ---
def download_song_pytube(video_url, display_title, download_path_base):
    import yt_dlp
    try:
        url = video_url

        if check_ffmpeg_available():
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'./music/%(title)s.%(ext)s',  # 下載檔名為影片標題
                'postprocessors': [{  # 轉檔設定
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': False,
            }
        else:
            ydl_opts = {
                'format': 'best',
                'outtmpl': f'./music/%(title)s.mp3',  # 下載檔名為影片標題
            }


        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        return True, f"下載完成: {display_title} (存為 {download_path_base})"
    except Exception as e:
        return False, f"下載 '{display_title}' 失敗: {str(e)}"

# --- Streamlit App Interface ---
st.set_page_config(page_title="YouTube Music 下載器", layout="wide")
st.title("🎵 YouTube Music 歌曲下載器")
st.caption("一個使用 Streamlit、Selenium 和 Pytube 從 YouTube Music 搜尋並下載歌曲的工具。")

# Initialize Selenium WebDriver in session state if not already present
if 'driver' not in st.session_state:
    try:
        with st.spinner("正在初始化瀏覽器驅動程式..."):
            st.session_state.driver = get_driver()
    except Exception as e:
        st.error(f"無法初始化 Selenium WebDriver: {e}")
        st.error("請確保 Chrome 瀏覽器已安裝，且網路連線正常。您可能需要重新啟動應用程式。")
        st.stop() # Stop execution if driver fails

# Initialize other session state variables
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_songs_ids' not in st.session_state: # Store IDs of selected songs
    st.session_state.selected_songs_ids = [] 

# --- UI for Search (in sidebar) ---
with st.sidebar:
    st.header("⚙️ 搜尋設定")
    search_query = st.text_input("輸入歌曲名稱、藝人或關鍵字:", placeholder="例如：江蕙, 告五人, 月亮代表我的心")
    search_type_display = st.radio("搜尋類型:", ('關鍵字搜尋', '藝人名稱'), index=0)
    # Convert display name to a simpler internal type
    search_type = "藝人" if search_type_display == '藝人名稱' else "關鍵字"
    
    max_songs_to_fetch = st.slider("最大搜尋結果數量 (歌曲數):", 5, 50, 20, 5)

    if st.button("🔍 搜尋歌曲", type="primary"):
        if not search_query:
            st.warning("請輸入搜尋內容。")
        else:
            with st.spinner(f"正在搜尋 '{search_query}'... 請耐心等候..."):
                try:
                    results = search_yt_music_songs(st.session_state.driver, search_query, search_type, max_songs_to_fetch)
                    st.session_state.search_results = results
                    st.session_state.selected_songs_ids = [] # Clear previous selections on new search
                    if not results:
                        st.info("找不到任何相關歌曲。請嘗試不同的關鍵字，或檢查 YouTube Music 網站是否可正常訪問。")
                except Exception as e:
                    st.error(f"搜尋過程中發生未預期的錯誤: {e}")
                    st.session_state.search_results = []
            if st.session_state.search_results:
                 st.success(f"搜尋完成！找到 {len(st.session_state.search_results)} 首歌曲。請在主面板中選擇要下載的歌曲。")

# --- Display Search Results and Selection ---
if st.session_state.search_results:
    st.subheader("📋 搜尋結果:")
    
    # Form for selecting songs
    with st.form(key='song_selection_form'):
        # Store the state of checkboxes within this form submission
        current_checkbox_states = {} 

        # Headers for the song list
        header_cols = st.columns([4, 3, 1]) # Title, Artist, Select Checkbox
        header_cols[0].markdown("**歌曲名稱**")
        header_cols[1].markdown("**path**")
        header_cols[2].markdown("**選擇**")

        # Display each song with a checkbox
        for idx, song in enumerate(st.session_state.search_results):
            data_cols = st.columns([4, 3, 1])
            data_cols[0].text(song['title'])
            data_cols[1].html(f"<a href={song['url']} target='_blank'>{song['url']}</a>")
           
            # Checkbox.value is True if song ID was previously selected
            is_selected_default = song['id'] in st.session_state.selected_songs_ids
            current_checkbox_states[song['id']] = data_cols[2].checkbox("", value=is_selected_default, key=f"cb_{song['id']}")

        
        form_submit_button = st.form_submit_button("💾 更新選擇並準備下載")

        if form_submit_button:
            # After form submission, update the master list of selected song IDs
            st.session_state.selected_songs_ids = [song_id for song_id, is_checked in current_checkbox_states.items() if is_checked]
            
            if not st.session_state.selected_songs_ids:
                st.warning("您尚未選擇任何歌曲。請勾選歌曲後再試。")
            else:
                st.success(f"已更新選擇！共選擇了 {len(st.session_state.selected_songs_ids)} 首歌曲準備下載。")

    # --- Download Section (shown if songs are selected) ---
    if st.session_state.selected_songs_ids:
        st.markdown("---")
        st.subheader(f"🎶 已選擇 {len(st.session_state.selected_songs_ids)} 首歌曲進行下載:")
        
        # List selected songs for confirmation
        selected_song_details_to_download = []
        for song_id_to_dl in st.session_state.selected_songs_ids:
            song_detail = next((s for s in st.session_state.search_results if s['id'] == song_id_to_dl), None)
            if song_detail:
                selected_song_details_to_download.append(song_detail)
                st.write(f"{song_detail['title']}")

        if st.button("⬇️ 開始下載選定的歌曲", type="primary", key="download_button"):
            if not selected_song_details_to_download:
                st.warning("沒有有效的歌曲被選擇以下載。")
            else:
                st.info(f"準備下載 {len(selected_song_details_to_download)} 首歌曲...")
                progress_bar = st.progress(0)
                # Create placeholders for status messages for each download
                status_placeholders = [st.empty() for _ in range(len(selected_song_details_to_download))]
                
                total_songs_to_download = len(selected_song_details_to_download)
                successfully_downloaded_count = 0

                # Use ThreadPoolExecutor for concurrent downloads (max_workers can be adjusted)
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # Map futures to song info and its status placeholder index
                    future_to_song_info = {
                        executor.submit(download_song_pytube, song['url'], song['title'], DOWNLOAD_PATH): (idx, song)
                        for idx, song in enumerate(selected_song_details_to_download)
                    }
                    
                    for i, future_task in enumerate(future_to_song_info):
                        idx, song_info_for_future = future_to_song_info[future_task]
                        try:
                            success_status, message_from_download = future_task.result() # Wait for download to complete
                            if success_status:
                                status_placeholders[idx].success(message_from_download)
                                successfully_downloaded_count += 1
                            else:
                                status_placeholders[idx].error(message_from_download)
                        except Exception as exc_dl:
                            status_placeholders[idx].error(f"下載 '{song_info_for_future['title']}' 時發生嚴重錯誤: {exc_dl}")
                        
                        # Update overall progress bar
                        progress_bar.progress((i + 1) / total_songs_to_download)

                st.success(f"所有選定歌曲的下載嘗試均已完成。成功下載 {successfully_downloaded_count}/{total_songs_to_download} 首歌曲。")
                st.balloons()
                # Optionally, clear selection after download
                # st.session_state.selected_songs_ids = [] 
                # st.experimental_rerun() # To update UI if selection is cleared

else:
    st.info("👋 請在左側邊欄輸入搜尋條件並點擊「搜尋歌曲」按鈕以開始。")

# --- Footer and Cleanup ---
st.markdown("---")
st.caption("📝 注意：下載受版權保護的音樂可能涉及法律問題。請確保您擁有下載相關內容的權利。此工具僅供技術研究與個人合理使用。")

if st.sidebar.button("🧹 清理並關閉瀏覽器驅動程式"):
    with st.spinner("正在關閉瀏覽器驅動程式..."):
        close_driver()
    st.sidebar.success("瀏覽器驅動程式已成功關閉。")
    st.sidebar.info("若要重新開始，請重新整理頁面以啟動新的驅動程式。")