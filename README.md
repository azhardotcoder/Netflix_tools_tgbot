# Netflix Cookie Checker Tool 🍪

A powerful and feature-rich tool for checking Netflix cookies with multiple modes and advanced features.

## ✨ Features

- **Multiple Checking Modes:**
  - 🚀 Fast Cookie Checker (Safe Mode) - Smart delays and IP protection
  - 📊 Detailed Cookie Checker - Full account information
  - 🌐 Fast Cookie Checker (Proxy Mode) - Check using proxy servers

- **File Handling Options:**
  - 📂 GUI File Browser (Select Multiple)
  - 🔍 Auto-detect from current folder
  - ⌨️ Manual path entry
  - 👀 Folder monitoring for new files

- **Advanced Features:**
  - 🔔 Telegram notifications support
  - 🌍 Proxy support with configuration
  - 💾 Auto-save valid cookies
  - 🔄 Smart rate limiting to avoid IP blocks

## 🛠️ Installation

1. Clone the repository:
```bash
git clone https://github.com/azhardotcoder/nf_cookies_checker.git
cd nf_cookies_checker
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

## 📝 Usage

1. Run the tool:
```bash
python index.py
```

2. Choose your preferred checking mode:
   - Fast Cookie Checker (Safe Mode)
   - Detailed Cookie Checker
   - Fast Cookie Checker (Proxy Mode)

3. Select cookie files using any of the available methods:
   - File Browser
   - Auto-detect
   - Manual entry
   - Folder monitoring

## 📋 Input File Format

The tool supports multiple cookie formats:
1. Raw cookie string
2. Bulk format with "Cookie = [value]"
3. Full account info format:
```
email:password | MemberSince = [date] | Country = [country] | ... | Cookie = [cookie_value]
```

## ⚙️ Configuration

- **Telegram Notifications:**
  - Set your bot token in `TELEGRAM_BOT_TOKEN`
  - Set your chat ID in `TELEGRAM_CHAT_ID`

- **Proxy Settings:**
  - Configure proxy settings in Proxy Mode
  - Supports multiple proxy formats

## 🔒 Security Features

- Smart delays between requests
- IP protection mechanisms
- Proxy support for anonymity
- Rate limiting to avoid detection

## 📦 Dependencies

- requests
- aiohttp
- colorama
- beautifulsoup4
- tkinter (included with Python)

## ⚠️ Disclaimer

This tool is for educational purposes only. Use it responsibly and in accordance with Netflix's terms of service.

## 🤝 Contributing

Feel free to open issues or submit pull requests to improve the tool.

## 📜 License

This project is licensed under the MIT License. 