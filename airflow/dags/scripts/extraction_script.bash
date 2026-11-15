#!/usr/bin/env bash
#
# extraction_script.bash <data_path> <base_url> <reference_year>
#
# Downloads {reference_year}.zip into <data_path>/loading_zone and unpacks it
# into <data_path>/raw/{reference_year}/ (the layout raw_parser expects).
#
# Guarantees:
#   - The download goes to a temp file first and is atomically moved into
#     place only when curl succeeds AND the file is non-empty.
#   - Extraction goes to a temp directory first; the raw/{year} directory is
#     only replaced when the archive passes an integrity test and unpacks
#     cleanly. A corrupt zip never leaves a half-extracted directory behind
#     and the downloaded zip is kept for inspection.
#   - A JSON outcome record is written to loading_zone/{year}.json with the
#     year, status (success | download_failed | extract_failed), zip path,
#     CSV count and failure reason.
#   - Re-runs skip the work when a success record exists and both the zip and
#     the extracted directory are still present.
#   - Exit code is non-zero on any failure so Airflow retries the task.

set -u

data_path=$1
base_url=$2
reference_year=$3

loading_zone="${data_path}/loading_zone"
raw_dir="${data_path}/raw"
zip_file="${loading_zone}/${reference_year}.zip"
record_file="${loading_zone}/${reference_year}.json"
extract_dir="${raw_dir}/${reference_year}"
tmp_zip="${loading_zone}/.${reference_year}.zip.tmp"
tmp_extract="${raw_dir}/.extract_${reference_year}.tmp"
download_url="${base_url}${reference_year}.zip"

mkdir -p "${loading_zone}" "${raw_dir}"

write_record() {
    # write_record <status> <csv_count> <failure_reason_json: string or null>
    local status=$1
    local csv_count=$2
    local reason=$3
    cat > "${record_file}" <<EOF
{
  "year": ${reference_year},
  "status": "${status}",
  "zip_path": "${zip_file}",
  "csv_count": ${csv_count},
  "failure_reason": ${reason}
}
EOF
    echo "Outcome record written to ${record_file}"
}

count_csvs() {
    # Count files the downstream raw_parser glob (raw/{year}/*.CSV) would see.
    find "${extract_dir}" -maxdepth 1 -name '*.CSV' -type f 2>/dev/null | wc -l
}

# --- Skip logic: previous run already succeeded and artifacts are intact ----
if [ -f "${record_file}" ] \
    && grep -q '"status": "success"' "${record_file}" \
    && [ -s "${zip_file}" ] \
    && [ -d "${extract_dir}" ] \
    && [ "$(count_csvs)" -gt 0 ]; then
    echo "Year ${reference_year} already extracted successfully, skipping."
    exit 0
fi

# --- Download: temp file first, atomic move on success ----------------------
echo "Downloading ${download_url} ..."
rm -f "${tmp_zip}"
if ! curl -fSL --retry 3 --connect-timeout 30 -o "${tmp_zip}" "${download_url}"; then
    rm -f "${tmp_zip}"
    echo "ERROR: download of ${download_url} failed" >&2
    write_record "download_failed" 0 "\"curl failed to download ${download_url}\""
    exit 1
fi
if [ ! -s "${tmp_zip}" ]; then
    rm -f "${tmp_zip}"
    echo "ERROR: downloaded file is empty: ${download_url}" >&2
    write_record "download_failed" 0 "\"downloaded file is empty\""
    exit 1
fi
mv -f "${tmp_zip}" "${zip_file}"
echo "Downloaded to ${zip_file}"

# --- Unpack: temp dir first, swap into raw/ only on success -----------------
echo "Unpacking ${zip_file} ..."
rm -rf "${tmp_extract}"
mkdir -p "${tmp_extract}"
# unzip exit codes: 0 = ok, 1 = warnings only (completed fine), >= 2 = error.
unzip -tqq "${zip_file}" >/dev/null 2>&1
test_rc=$?
unzip -o "${zip_file}" -d "${tmp_extract}" >/dev/null
unzip_rc=$?
if [ "${test_rc}" -gt 1 ] || [ "${unzip_rc}" -gt 1 ]; then
    rm -rf "${tmp_extract}"
    echo "ERROR: archive is corrupted or unzip failed: ${zip_file}" >&2
    write_record "extract_failed" 0 "\"zip archive is corrupted or unzip failed\""
    exit 1
fi

# INMET archives usually contain a top-level {year}/ folder; accept flat
# archives too. Either way the CSVs must end up in raw/{year}/ for raw_parser.
if [ -d "${tmp_extract}/${reference_year}" ]; then
    rm -rf "${extract_dir}"
    mv "${tmp_extract}/${reference_year}" "${extract_dir}"
    rm -rf "${tmp_extract}"
else
    rm -rf "${extract_dir}"
    mkdir -p "${extract_dir}"
    shopt -s dotglob nullglob
    mv "${tmp_extract}"/* "${extract_dir}/"
    shopt -u dotglob nullglob
    rm -rf "${tmp_extract}"
fi

csv_count=$(count_csvs)
echo "Extracted ${csv_count} CSV file(s) to ${extract_dir}"
write_record "success" "${csv_count}" "null"
