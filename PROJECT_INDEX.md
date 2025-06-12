# Netflix Cookie Checker - Project Index

## Project Overview
A Python-based tool for checking Netflix cookies with multiple operation modes and Telegram bot integration.

## Core Components

### 1. Cookie Checking Module (`checkers.py`)
- Main cookie validation logic
- Supports multiple checking modes:
  - Safe Fast Checker
  - Detailed Checker
  - Proxy Mode Checker
- Asynchronous cookie validation using `aiohttp`
- Result saving and formatting functionality

### 2. Telegram Bot Integration (`bot.py`)
- Handles user interactions via Telegram
- Command processing and response management
- User authorization and admin features
- File handling for cookie submissions

### 3. Utility Modules

#### File Utilities (`file_utils.py`)
- Temporary file management
- File combination operations

#### General Utilities (`utils.py`)
- Random header generation
- Console output formatting using Colorama

#### Configuration (`config.py`)
- Telegram bot configuration
- User authorization settings
- API tokens and chat IDs

## Dependencies

### Core Libraries
- `aiohttp`: Asynchronous HTTP client/server
- `telegram`: Telegram Bot API integration
- `colorama`: Console text formatting
- `requests`: HTTP requests handling

### System Requirements
- Python 3.x
- Async/await support
- Internet connectivity

## Key Features

### Cookie Validation
- Multiple validation modes
- Asynchronous processing
- Rate limiting and delay patterns
- Proxy support

### Bot Features
- File upload support
- Command-based interaction
- User authorization
- Admin notifications

### File Management
- Temporary file handling
- Result file generation
- Cookie file parsing

## Security Features
- User authorization
- Admin-only commands
- Secure file handling
- Temporary file cleanup

## Build Configuration
- PyInstaller spec file included
- Custom icon integration
- Console-based executable