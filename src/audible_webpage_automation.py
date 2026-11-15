
import time
import re
import audible_data_handling as adh
from selenium import webdriver
from selenium.webdriver.common.keys import Keys

from selenium.webdriver.chrome.options import Options

book_title = ""
book_author = ""
book_narrator = ""
audio_runtime = ""
audiobook_type = ""
categories = ""
rating = ""
book_price = ""
reviews = ""
final_reviews_list = []
book_data_list = []
showmore_open_times = 0


def audible_homepage_open(audible_homepage_link, audible_link, open_times):
    global showmore_open_times
    showmore_open_times = open_times
    chrome_options = Options()
    chrome_options.add_extension('C:/chropath/extension_6_1_11_0.crx')
    driver = webdriver.Chrome(executable_path="C:/chromedriver/chromedriver.exe", chrome_options=chrome_options)
    driver.get(audible_homepage_link)
    driver.get(audible_link)
    return click_element(driver)


def click_element(driver):
    """Walk every product card on every page.

    Each book is processed in isolation: a failure on one book is turned
    into a list-fallback row and the loop continues. Pagination is an
    explicit loop (no recursion) and reaching the end / hitting an error
    always prints a clear message instead of exiting silently.
    """
    page_number = 1
    while True:
        try:
            product_list = driver.find_elements_by_xpath("//div[contains(@data-widget,'productList')]/li")
        except Exception as e:
            print("Pagination aborted on page %d: failed to read the product list (%s)" % (page_number, e))
            break

        length_of_product_list = len(product_list)
        if length_of_product_list == 0:
            print("No products found on page %d; stopping pagination." % page_number)
            break

        # range starts at 1 on purpose: the xpath index is 1-based, so the
        # very first card (li[1]) is no longer skipped.
        for item in range(1, length_of_product_list + 1):
            try:
                book_link = driver.find_element_by_xpath(
                    "//div[contains(@data-widget,'productList')]/li[" + str(item) + "]//li[1]//a")
            except Exception as e:
                print("Book %d on page %d could not be opened from the list (%s); skipped."
                      % (item, page_number, e))
                continue
            try:
                tab_switch(driver, book_link, item)
            except Exception as e:
                # Last-resort guard: make sure we are back on the list tab
                # so the remaining books and the next page keep working.
                print("Unexpected failure while handling book %d on page %d (%s); recovering to list page."
                      % (item, page_number, e))
                recover_to_list_tab(driver)
            print("end of " + (book_link.text if book_link.text else "book %d" % item))

        try:
            nextButton = driver.find_element_by_xpath("//span[contains(@class,'nextButton')]")
            if nextButton.is_enabled():
                nextButton.click()
                page_number += 1
                continue
            print("End of list reached on page %d: next button is disabled." % page_number)
            break
        except Exception as e:
            print("End of list on page %d: next button not found or not clickable (%s)." % (page_number, e))
            break


def recover_to_list_tab(driver):
    """Close any stray detail tab and make sure the list tab is active."""
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            driver.switch_to.window(handles[1])
            driver.close()
    except Exception:
        pass
    try:
        driver.switch_to.window(driver.window_handles[0])
    except Exception:
        pass


def tab_switch(driver, book_link, item):
    book_link.send_keys(Keys.CONTROL + Keys.ENTER)
    driver.switch_to.window(driver.window_handles[1])
    fetch_element_data(driver, item)


def tab_close(driver):
    driver.close()
    driver.switch_to.window(driver.window_handles[0])


def _safe_detail_text(driver, xpath):
    try:
        return driver.find_element_by_xpath(xpath).text
    except Exception:
        return ""


def fetch_element_data(driver, item):
    """Scrape one detail page.

    Success path: all required detail fields are present, reviews are
    crawled and the row is marked ``detail_page``.
    Failure path: any required field missing -> a row is assembled from
    the list card (missing fields blank), marked ``list_fallback``.
    Either way the detail tab is closed and the driver returns to the
    list tab, so subsequent books and pagination keep running.
    """
    total_review_columns = showmore_open_times * 10
    adh.review_column_creator(total_review_columns)

    detail_row = None
    try:
        book_title = (driver.find_element_by_xpath("//div[contains(@class,'centerSlot')][2]/div//li[1]")).text
        book_subtitle = _safe_detail_text(driver, "//div[contains(@class,'centerSlot')][2]/div//li[2]")
        book_author = (driver.find_element_by_xpath(
            "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'authorLabel')]/a")).text
        book_narrator = (driver.find_element_by_xpath(
            "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'narratorLabel')]/a")).text
        audio_runtime = (driver.find_element_by_xpath(
            "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'runtimeLabel')]")).text
        audiobook_type = (driver.find_element_by_xpath(
            "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'format')]")).text
        categories = _safe_detail_text(
            driver, "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'categoriesLabel')]/a")
        rating = _safe_detail_text(
            driver, "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'ratingsLabel')]/span[2]")
        try:
            total_ratings = (driver.find_element_by_xpath(
                "//div[contains(@class,'centerSlot')][2]/div//li[contains(@class,'ratingsLabel')]")).text
            final_string = re.search(r'\((.*?)\)', total_ratings)
            ratings_cleaned = re.sub(r'[^0-9]', "", final_string.group(1))
        except Exception:
            ratings_cleaned = ""
        book_price = (driver.find_element_by_xpath(
            "//a[contains(@title," + "\"" + book_title + "\"" + ")]")).text
        p_audio_runtime, p_book_price = adh.data_preprocessing(audio_runtime, book_price)
        detail_row = [book_title, book_subtitle, book_author, book_narrator, p_audio_runtime,
                      audiobook_type, categories, rating, ratings_cleaned, p_book_price]
    except Exception as e:
        print("Detail page data missing for list item %d (%s); falling back to list-card data." % (item, e))
        detail_row = None

    try:
        if detail_row is not None:
            final_reviews_list = reviews_crawler(driver)
        else:
            # On fallback do not touch the (possibly broken) detail page;
            # the review columns stay empty but their count still matches
            # showmore_open_times * 10.
            final_reviews_list = [""] * total_review_columns
    except Exception as e:
        print("Review crawling failed for list item %d (%s); storing empty review columns." % (item, e))
        final_reviews_list = [""] * total_review_columns

    # The detail tab must always be closed before reading the list card,
    # and the driver must always return to the list tab afterwards.
    tab_close(driver)

    try:
        if detail_row is not None:
            adh.write_to_csv(detail_row, final_reviews_list, adh.SOURCE_DETAIL)
        else:
            fallback_row = data_not_found(driver, item)
            adh.write_to_csv(fallback_row, final_reviews_list, adh.SOURCE_FALLBACK)
    except Exception as e:
        print("Failed to persist row for list item %d (%s); continuing with the remaining books." % (item, e))


def reviews_crawler(driver):
    """Collect review texts, always returning a list of the exact length
    showmore_open_times * 10 (blank-padded when fewer reviews exist)."""
    local_reviews_list = []
    total_review_columns = showmore_open_times * 10
    adh.review_column_creator(total_review_columns)
    try:
        review_list = driver.find_elements_by_xpath("//div[contains(@class,'ReviewsTabUS')]/div/div[2]/p[1]")
        if not review_list:
            local_reviews_list = [""] * total_review_columns
        else:
            for review_item in range(1, showmore_open_times):
                time.sleep(3)
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                more_reviews = driver.find_element_by_xpath("//span[contains(@class,'showMoreReviews')]")
                more_reviews.click()
            time.sleep(5)
            review_list = driver.find_elements_by_xpath("//div[contains(@class,'ReviewsTabUS')]/div/div[2]/p[1]")
            for item in range(1, len(review_list)):
                reviews = driver.find_element_by_xpath(
                    "//div[contains(@class,'ReviewsTabUS')]/div[" + str(item) + "]/div[2]/p[1]")
                review_cleaned = re.sub(r'[^a-zA-Z0-9()\[\]\{\}.,!?\' */\"]', "", reviews.text)
                local_reviews_list.append(review_cleaned)
                if item == len(review_list) - 1:
                    last_item = int(len(review_list))
                    reviews = driver.find_element_by_xpath(
                        "//div[contains(@class,'ReviewsTabUS')]/div[" + str(last_item) + "]/div[2]/p[1]")
                    review_cleaned = re.sub(r'[^a-zA-Z0-9()\[\]\{\}.,!?\' */\"]', "", reviews.text)
                    local_reviews_list.append(review_cleaned)
    except Exception as e:
        print("Review collection interrupted (%s); keeping the reviews gathered so far." % e)

    if len(local_reviews_list) < total_review_columns:
        local_reviews_list = local_reviews_list + [""] * (total_review_columns - len(local_reviews_list))
    else:
        local_reviews_list = local_reviews_list[:total_review_columns]
    return local_reviews_list


def _safe_list_text(driver, item, fragment):
    xpath = ("//div[contains(@data-widget,'productList')]/li[" + str(item) + "]" + fragment)
    try:
        return driver.find_element_by_xpath(xpath).text
    except Exception:
        return ""


def data_not_found(driver, item):
    """Assemble a row from the list card only.

    Every field is fetched independently: a missing element yields an
    empty string instead of aborting the fallback, so one damaged card
    still produces a row and the crawl can continue.
    """
    book_title = _safe_list_text(driver, item, "//h3[contains(@class,'heading')]")
    book_subtitle = _safe_list_text(driver, item, "//li[contains(@class,'subtitle')]")
    book_author = _safe_list_text(driver, item, "//li[contains(@class,'authorLabel')]//a")
    book_narrator = _safe_list_text(driver, item, "//li[contains(@class,'narratorLabel')]//a")
    audio_runtime = _safe_list_text(driver, item, "//li[contains(@class,'runtimeLabel')]")
    audiobook_type = ""
    categories = ""
    rating = _safe_list_text(driver, item, "//li[contains(@class,'ratingsLabel')]/span[1]")
    total_ratings = rating
    ratings_cleaned = re.sub(r'[^0-9]', "", total_ratings) if total_ratings else ""
    book_price = _safe_list_text(driver, item, "//div[contains(@class,'BuyBox')]/p[1]")
    p_audio_runtime, p_book_price = adh.data_preprocessing(audio_runtime, book_price)
    book_data_list = [book_title, book_subtitle, book_author, book_narrator, p_audio_runtime,
                      audiobook_type, categories, rating, ratings_cleaned, p_book_price]

    return book_data_list
