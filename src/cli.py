"""This module contains the logic to find trains from the CLI."""

import argparse
from typing import Any, Dict, List, Tuple

from rich.box import HEAVY
from rich.console import Console
from rich.table import Table

from models import TrainRideRecord
from scraper import Scraper
from validators import validate_station, validate_date


def format_transfers(train: TrainRideRecord) -> str:
    """Returns a rich-formatted description of whether the train is direct or has transfers"""
    if train.is_direct:
        return "[green]Direct[/]"
    label = "1 transfer" if train.transfers == 1 else f"{train.transfers} transfers"
    return f"[yellow]{label}[/]\n" + "\n".join(train.transfer_stations)


def format_price(train: TrainRideRecord, mean_price: float) -> str:
    """Returns the rich-formatted price, highlighting it if it's cheaper than the mean price"""
    if not train.available:
        return "[yellow]Not on sale[/]"
    if train.price < mean_price:
        return f"[bold green]{train.price:.02f} €[/]"
    return f"{train.price:.02f} €"


def remove_duplicates(trains: List[TrainRideRecord]) -> List[TrainRideRecord]:
    """Merges trains that look the same to the user (same type, times and transfers).

    Renfe sometimes lists two trains with different train numbers for the same service. Only the
    most useful one is kept: on sale (the cheapest) first, then not on sale yet, then sold out.
    The original order of the trains is preserved.
    """

    def rank(train: TrainRideRecord) -> Tuple[int, float]:
        if train.available:
            return (0, train.price)
        return (1 if train.not_on_sale_yet else 2, 0.0)

    best: Dict[Tuple[Any, ...], TrainRideRecord] = {}
    for train in trains:
        key = (
            train.train_type,
            train.departure_time,
            train.arrival_time,
            tuple(train.transfer_stations),
        )
        if key not in best or rank(train) < rank(best[key]):
            best[key] = train
    return list(best.values())


def main(origin: str, destination: str, departure_date: str, direct_only: bool = False):
    """Searches for available train rides between the specified origin and destination stations
    on the given departure date, and displays the results in a formatted table.

    The function validates the input parameters, scrapes train ride data, and prints a table
    with train type, departure and arrival times, duration, transfers and price. Trains whose sales
    haven't opened yet are listed as "Not on sale", while sold out trains are hidden. If no
    trains are found, a message is displayed. If direct_only is set, trains with transfers are
    discarded. Duplicated trains (same type, times and transfers) are shown only once.
    """

    print("\n")  # Padding line

    console = Console()

    ctx = {
        "origin": validate_station(origin),
        "destination": validate_station(destination),
        "departure_date": validate_date(departure_date),
    }

    for validations in ctx.values():
        if not validations:
            console.print(f"[i][red]{validations.error_message}[/i]")
            return

    scraper = Scraper(ctx["origin"].station, ctx["destination"].station, ctx["departure_date"].date)

    trains = scraper.get_trainrides()

    if direct_only:
        trains = [train for train in trains if train.is_direct]

    trains = remove_duplicates(trains)

    if not trains:
        console.print("[i][red]No trains found for the given filter[/i]")
        return

    # Trains not on sale yet are shown too, so users can check when Renfe opens their sales
    shown_trains = [train for train in trains if train.available or train.not_on_sale_yet]

    if not shown_trains:
        if len(trains) == 1:
            message = "The only train for the given filter is sold out"
        else:
            message = f"All {len(trains)} trains for the given filter are sold out"
        console.print(f"[i][red]{message}[/i]")
        return

    table = Table(
        header_style="bold blue",
        title=f"Trains from {origin} to {destination} - {departure_date}",
        box=HEAVY,
    )

    table.add_column("Train type")
    table.add_column("Departure time", justify="center", min_width=5)
    table.add_column("Arrival time", justify="center", min_width=5)
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Transfers", no_wrap=True, overflow="ellipsis", max_width=20)
    table.add_column("Price", justify="right", no_wrap=True)

    # Unavailable trains have no price (NaN), so they must be left out of the mean
    prices = [train.price for train in shown_trains if train.available]
    mean_price = sum(prices) / len(prices) if prices else 0.0

    for train in shown_trains:
        table.add_row(
            train.train_type,
            f"{train.departure_time.strftime('%H:%M')}",
            f"{train.arrival_time.strftime('%H:%M')}",
            f"{train.duration / 60:.01f} h.",
            format_transfers(train),
            format_price(train, mean_price),
        )

    console.print(table)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="renfe-bot", description="Find trains from the CLI")

    parser.add_argument(
        "-o", "--origin", required=True, help="Origin station code or name (required)"
    )
    parser.add_argument(
        "-d", "--destination", required=True, help="Destination station code or name (required)"
    )
    parser.add_argument(
        "--departure_date", required=True, help="Departure date in DD/MM/YYYY format (required)"
    )
    parser.add_argument(
        "--direct", action="store_true", help="Show only direct trains, without transfers"
    )
    args = parser.parse_args()

    main(args.origin, args.destination, args.departure_date, args.direct)
