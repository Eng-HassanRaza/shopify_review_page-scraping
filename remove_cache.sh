#!/bin/bash
# Remove Python cache files from Git tracking

echo "Removing __pycache__ directories from Git..."
find . -type d -name "__pycache__" -not -path "./venv/*" -not -path "./.shopifyenv/*" -exec git rm -r --cached {} \; 2>/dev/null

echo "Removing .pyc files from Git..."
find . -name "*.pyc" -not -path "./venv/*" -not -path "./.shopifyenv/*" -exec git rm --cached {} \; 2>/dev/null

echo "Removing .pyo files from Git..."
find . -name "*.pyo" -not -path "./venv/*" -not -path "./.shopifyenv/*" -exec git rm --cached {} \; 2>/dev/null

echo "Done! Files removed from Git tracking (but kept locally)."
echo "Run: git commit -m 'Remove Python cache files'"
