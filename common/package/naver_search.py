import requests
import random


def generate_user_agent():
    browsers = ["Chrome", "Firefox", "Safari", "Opera", "Edge"]
    os_versions = ["Windows NT 10.0", "Windows NT 6.1", "Windows NT 6.3", "Macintosh; Intel Mac OS X 10_15_7", "X11; Linux x86_64"]
    chrome_versions = ["58.0.3029.110", "60.0.3112.113", "63.0.3239.132", "67.0.3396.87", "68.0.3440.84", "69.0.3497.100"]
    
    browser = random.choice(browsers)
    os_version = random.choice(os_versions)
    chrome_version = random.choice(chrome_versions) if browser == "Chrome" else ""
    
    user_agent = f"Mozilla/5.0 ({os_version}) AppleWebKit/537.36 (KHTML, like Gecko) {browser}/{chrome_version} Safari/537.36"
    
    return user_agent


def get_search_data(url, formatted_ip_list):
    user_agent = generate_user_agent()
    ip_address = random.choice(formatted_ip_list)

    headers = {
        "User-Agent": user_agent,
        "X-Forwarded-For": ip_address,
        "Referer": "https://www.naver.com/",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    response = requests.get(url, headers=headers, timeout=15)

    return response, ip_address
