
import csv
import os
import re

final_data_list = []
book_data_list = []
final_reviews_list = []
book_data_columns = ["Book Title", "Book Subtitle", "Book Author", "Book Narrator", "Audio Runtime", "Audiobook_Type", "Categories", "Rating", "Total No. of Ratings", "Price"]
book_review_columns = []
total_review_count = ""
final_column_data = []

# Marker appended as the last column so a row can be traced back to either
# the full detail page or the list-card fallback.
SOURCE_COLUMN = "Data Source"
SOURCE_DETAIL = "detail_page"
SOURCE_FALLBACK = "list_fallback"


def data_preprocessing(length, price):
    """Clean the runtime ('Length: ...') and the price label.

    Any missing/unexpected value simply comes back as an empty string
    instead of raising, so a partially populated list card never kills
    the whole book row.
    """
    n_length = re.sub('Length: ', '', length or "")
    n_price = ""
    price_sub = price or ""
    if "Pre" in price_sub:
        n_price = re.sub('Pre-order for ', '', price_sub)
    elif "Reg" in price_sub:
        n_price = re.sub('Regular price: ', '', price_sub)
    elif "Buy" in price_sub:
        n_price = re.sub('Buy for ', '', price_sub)
    else:
        n_price = price_sub
    return n_length, n_price


def review_column_creator(review_list):
    """Build the header (book columns + one column per review slot).

    Idempotent: calling it once per book does not keep growing the
    header. ``review_list`` is the number of review columns required,
    i.e. showmore_open_times * 10.
    """
    global final_column_data
    if len(final_column_data) == 0 and review_list and review_list > 0:
        book_review_columns = []
        total_review_counts = review_list
        for item in range(1, total_review_counts):
            book_review_columns.append("Review " + str(item))
            if item == total_review_counts - 1:
                book_review_columns.append("Review" + str(item + 1))
        final_column_data = book_data_columns + book_review_columns + [SOURCE_COLUMN]
    return


def write_to_csv(book_data_list, final_reviews_list, data_source=SOURCE_DETAIL):
    """Append one book row to output_encoded.csv.

    The review list is normalised to exactly the number of review
    columns declared in the header, and the data-source marker is
    appended as the final column.
    """
    global final_data_list
    review_slot_count = len(final_column_data) - len(book_data_columns) - 1
    if review_slot_count < 0:
        review_slot_count = 0
    reviews = list(final_reviews_list)
    if len(reviews) < review_slot_count:
        reviews = reviews + [""] * (review_slot_count - len(reviews))
    else:
        reviews = reviews[:review_slot_count]
    final_data_list = list(book_data_list) + reviews + [data_source]
    csv_file_path = "output_encoded.csv"

    try:
        file_exists = os.path.exists(csv_file_path)
        need_header = (not file_exists) or os.path.getsize(csv_file_path) == 0
        with open(csv_file_path, "a", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file, delimiter=",")
            if need_header:
                writer.writerow(final_column_data)
            writer.writerow(final_data_list)
    except Exception as e:
        print("Failed to write row to %s: %s" % (csv_file_path, e))

    return


def duplicate_remover():
    """Deduplicate output_encoded.csv into Audible_Dataset_final.csv.

    The header is written exactly once; fully duplicate data rows are
    dropped. A clear summary (or a missing-file message) is printed
    instead of failing silently.
    """
    input_file_name = "output_encoded.csv"
    output_file_name = "Audible_Dataset_final.csv"

    if not os.path.exists(input_file_name):
        print("Dedup skipped: input file '%s' does not exist." % input_file_name)
        return

    seen = set()
    header = None
    unique_rows = 0
    duplicate_rows = 0
    with open(input_file_name, 'r', encoding="utf-8", newline="") as in_file, \
            open(output_file_name, 'w', encoding="utf-8", newline="") as out_file:
        reader = csv.reader(in_file)
        writer = csv.writer(out_file, delimiter=",")
        for row in reader:
            if not row:
                continue
            if header is None:
                header = row
                writer.writerow(header)
                continue
            # A repeated header row (e.g. file appended across runs) is a duplicate, not data.
            if row == header:
                duplicate_rows += 1
                continue
            row_key = tuple(row)
            if row_key in seen:
                duplicate_rows += 1
                continue
            seen.add(row_key)
            writer.writerow(row)
            unique_rows += 1

    print("Dedup finished: '%s' written with one header and %d unique data row(s); %d duplicate row(s) removed."
          % (output_file_name, unique_rows, duplicate_rows))
