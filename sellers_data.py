import sys

import PySimpleGUI as sg
import requests
from bs4 import BeautifulSoup


def _supplier_data_from_card(container) -> dict:
    """Extract a single supplier's data from a search-page factory card.

    Raises an exception on any failure (request error, missing contact
    link, ...) so the caller can record it and skip this supplier.
    """
    main_prods = container.find("ul", attrs={"class": "capability"})
    company_name = container.find("a", attrs={"target": "_blank"})
    company_website = (
        f'https:{company_name["href"] if company_name is not None else ""}'
    )
    get_website = requests.get(company_website)
    website_content = get_website.content
    soup = BeautifulSoup(website_content, "html.parser")
    dir_cont_link = [
        link["href"]
        for link in soup.findAll(
            "a",
            attrs={"class": "shop-component-KeyButton ContactSupplier message"},
            href=True,
        )
    ]
    if not dir_cont_link:
        raise ValueError("No contact supplier link found on the company page")
    return {
        "company_name": company_name.text if company_name is not None else "",
        "main_prods": main_prods.text if main_prods is not None else "",
        "company_website": company_website if company_name is not None else "",
        "dir_cont_link": dir_cont_link[0],
    }


def sellers_data(prd_name: str, suppliers_qty: int) -> dict:
    """Collect suppliers data for a product.

    Returns a dict with two keys:
      - "success": list of supplier records ready for export/contact.
      - "failed":  list of {"company_name", "error"} records for suppliers
        whose company page could not be processed; they are skipped and do
        not abort the batch.

    The search page itself (request failure or no results) is still treated
    as an overall failure and exits the program.
    """
    base_url = "https://www.alibaba.com/corporations/"

    try:
        prod_search_content = requests.get(f"{base_url}{prd_name}.html").content
    except Exception as e:
        sg.popup("Error", f"Error while fetching the search page: {e}")
        sys.exit(1)
    soup = BeautifulSoup(prod_search_content, "html.parser")
    prod_containers = soup.findAll("div", attrs={"class": "factory-card"})
    if prod_containers == []:
        sg.popup("No results found for this product", "Please, try another product.")
        sys.exit(1)

    success_list = []
    failed_list = []

    # Get the data from each supplier and prevent to crash if doesn't find enough sellers
    for _, container in zip(range(suppliers_qty), prod_containers):
        company_name_tag = container.find("a", attrs={"target": "_blank"})
        company_name = company_name_tag.text if company_name_tag is not None else ""
        try:
            success_list.append(_supplier_data_from_card(container))
        except Exception as e:
            failed_list.append({"company_name": company_name, "error": str(e)})
    return {"success": success_list, "failed": failed_list}
