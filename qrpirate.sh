#!/bin/bash

# Copyright (c) 2018 Marco Zollinger
# Licensed under MIT, the license file shall be included in all copies

# crawling search engines for images matching description
./qrcrawler.py "$1"
crawler_status=$?

mkdir -p ./qrbooty

# move a file from an engine subfolder into the qrbooty root folder without
# ever overwriting an image that is already there. on a name clash both
# files are kept, the newcomer gets a numeric suffix.
move_unique() {
    source=$1
    prefix=$2
    filename=$(basename "$source")
    destination="./qrbooty/${prefix}_${filename}"
    if [ ! -e "$destination" ]; then
        mv "$source" "$destination"
        return
    fi
    name=${filename%.*}
    extension=${filename##*.}
    if [ "$name" = "$filename" ]; then
        extension=""
    else
        extension=".$extension"
    fi
    index=1
    while [ -e "./qrbooty/${prefix}_${name}_${index}${extension}" ]; do
        index=$((index + 1))
    done
    mv "$source" "./qrbooty/${prefix}_${name}_${index}${extension}"
}

for engine in google bing baidu; do
    for pathname in ./qrbooty/"$engine"/*; do
        [ -f "$pathname" ] || continue
        move_unique "$pathname" "$engine"
    done
done

# read private keys from QR codes in images
./qr2key.py

# remove duplicates
sort keylist.txt | uniq > keylist_unique.txt

if [ "$crawler_status" -ne 0 ]; then
    echo "qrpirate: qrcrawler reported a failed engine, see the summary above." >&2
    exit "$crawler_status"
fi
