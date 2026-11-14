# Krawler: A Multithreaded Web Crawler in Python

[![GitHub issues](https://img.shields.io/github/issues/habedi/Krawler.svg?style=plastic)](https://github.com/habedi/Krawler/issues) [![GitHub forks](https://img.shields.io/github/forks/habedi/Krawler.svg?style=plastic)](https://github.com/habedi/Krawler/network) [![GitHub stars](https://img.shields.io/github/stars/habedi/Krawler.svg?style=plastic)](https://github.com/habedi/Krawler/stargazers)

An implementation of a simple web crawler in Python. The crawler is fully multithreaded and can be used to crawl the web for a given domain name.

## Installing Poetry

To get started you need to have [Poetry](https://python-poetry.org/) installed. You can install
Poetry by running the following command in the shell.

```bash
pip install poetry
```

When the installation is finished, run the following command in the shell in the root folder of this repository to
install the dependencies and create a virtual environment for the project.

```bash
poetry install
```

After that, enter the Poetry environment by invoking the poetry shell command.

```bash
poetry shell
```

## Installing System Dependencies

If you are using a Debian-based system, you can install the system-wide dependencies by running the following command.

```bash
sudo apt-get install python3-bs4 libnss-resolve nscd
```

## Running the Crawler

To run the crawler, you can use the following command.

```bash
pushd src && python3 main.py --domain <domain_name> --threads <number_of_threads> --output <output_file> && popd
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
