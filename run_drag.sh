#!/bin/bash

cd "$(dirname "$0")"

echo "Copying files..."

for file in "$@"
do
  cp "$file" input/
done

echo "Processing PDFs..."
python3 -m app.main

echo "Done. Press any key to close."
read
