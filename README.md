###Sanjoy Kumar Biswas

###Scrape Job portal data

#### Running the MyCareersFuture scraper (`webscrapping.py`)

The scraper is failure-tolerant: a page that fails to load or fails the title
assertion is recorded as a failed page and collection continues; a job card
that cannot be opened or whose detail fields cannot be extracted is skipped
(page number and 1-based card index are recorded) while the rest of the run
continues.

Outputs:

- `analyst_job.csv` — successfully collected jobs (header-only if none succeeded)
- `scrape_outcome.json` — verifiable run summary containing `total_pages`,
  `successful_pages`, `failed_pages`, `success_count` and `skipped_jobs`

All previously hard-coded settings can be overridden with command line flags or
`MCF_*` environment variables (defaults in parentheses):

| Setting            | CLI flag            | Env variable           | Default                                  |
|--------------------|---------------------|------------------------|------------------------------------------|
| Chrome driver path | `--driver-path`     | `MCF_CHROMEDRIVER_PATH`| `/Users/janice/Developer/chromedriver`   |
| Page limit         | `--max-pages`       | `MCF_MAX_PAGES`        | `64`                                     |
| Cards per page     | `--cards-per-page`  | `MCF_CARDS_PER_PAGE`   | `20`                                     |
| Page wait (s)      | `--page-wait`       | `MCF_PAGE_WAIT`        | `6`                                      |
| Detail wait (s)    | `--detail-wait`     | `MCF_DETAIL_WAIT`      | `8`                                      |
| Back wait (s)      | `--back-wait`       | `MCF_BACK_WAIT`        | `3`                                      |
| Snackbar wait (s)  | `--snackbar-wait`   | `MCF_SNACKBAR_WAIT`    | `2`                                      |
| Output CSV         | `--output`          | `MCF_OUTPUT_CSV`       | `analyst_job.csv`                        |
| Outcome JSON       | `--outcome-file`    | `MCF_OUTCOME_FILE`     | `scrape_outcome.json`                    |
| Search keyword     | `--search-keyword`  | `MCF_SEARCH_KEYWORD`   | `analyst`                                |

If the driver is missing or Chrome fails to start, the script exits with code 1
and a clear error message instead of silently producing an empty table.

Offline verification (no network, no real browser — an injectable fake driver
is used):

```
python -m pytest tests/
```

Step 1 :

First go through the problem statement that which domain data I need to collect and which type of columns and information gather by scrape the site.


Step 2 :

Find the bunch of particular URL from where I scrape the information. For the problem statement I will choose indeed.com , monster.com, linkedin.com such type of job portal website.


Step 3 :

Inspect the web page: 
As a lot of information store in webpage, I don’t need all the information. On basis of problem statement I inspect the webpage and highlighted the HTML to get particular information form webpage. And findout the data I want to extract.


Step 4 : 

Extract the data from webpage :
Now extracting data from those website. For extracting data by web scraping I will choose Python library BeautifulSoup.


Step 5 :

After scraping data I will do preprocess every dataset Like , 
Date-Time : May be different dataset have different date time format. I will do make a several format .


Drop unnecessary columns . Drop null and duplicate rows. Scaling all the dataset . 


As every datatsets don not have all the Columns . I will do rescale the columns for all scraping datasets that’s all dataset contain same columns and can merge easily
Like : indeed.com [German] contain columns are company_name, job_title, city, years of experience, salary [Lets say missing columns- Skills]

Monster.com [German] contain columns are company_name, job_title, city, year_of_experience,skills [Lets say missing columns- Salary]

This time I will do which columns are common for those two (any number) datasets. 

For job portal scraping I get must having columns are Job_title & company_name

Now I will make a model data columns after merge which information we need.

Model columns : company_name, job_title, city, year_of_experience, skills,salary

Then I will merge those two dataset by those columns.


Step 6 : 

Fill missing value [skills] for indeed.com:
For fill missing rows I will apply machine learning model here. Like we have dataset of monster.com where I get skills columns. I will take this dataset for train the model and for testing I will apply indeed.com dataset. 
Fill missing value [salary] for monster.com:
For missing columns of salary at monster.com I will use any regression machine learning algorithm to fill those missing rows . And for train dataset I will take indeed.com data as its have salary columns.
