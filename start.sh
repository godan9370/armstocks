#!/bin/bash
# @RM!2T0CKS — start script (Mac / Linux)
cd "$(dirname "$0")"
pip install -r requirements.txt -q
echo ""
echo "  Starting @RM!2T0CKS..."
echo ""
python app.py
