#!/bin/bash
# 雙擊這個檔案：抓最新的羽球團資料，抓完自動打開網頁
cd "$(dirname "$0")" || exit 1
python3 scripts/fetch.py && open index.html
echo; read -n 1 -s -r -p "按任意鍵關閉視窗"
