import { Metadata } from "next";
import { NewsGrid } from "@/components/news-grid";
import { getNewsByRegion, getBreakingNews } from "@/lib/news-aggregator";
import { REGIONS, COUNTRIES } from "@/lib/constants";
import { notFound } from "next/navigation";
import Link from "next/link";
import { Globe2, MapPin, Newspaper } from "lucide-react";

interface RegionPageProps {
  params: Promise<{ region: string }>;
}

export async function generateMetadata({
  params,
}: RegionPageProps): Promise<Metadata> {
  const { region } = await params;
  const regionData = REGIONS.find((r) => r.id === region);
  if (!regionData) return { title: "Region Not Found" };

  return {
    title: `${regionData.name} News - Global News Daily`,
    description: `Latest news from ${regionData.name}. Breaking headlines from ${regionData.countries.length} countries.`,
  };
}

export async function generateStaticParams() {
  return REGIONS.map((region) => ({
    region: region.id,
  }));
}

export default async function RegionPage({ params }: RegionPageProps) {
  const { region } = await params;
  const regionData = REGIONS.find((r) => r.id === region);

  if (!regionData) {
    notFound();
  }

  // Get countries in this region
  const regionCountries = COUNTRIES.filter((c) => c.region === region);

  // Fetch news for the region
  const [breakingNews, regionalNews] = await Promise.all([
    getBreakingNews(5),
    getNewsByRegion(region, 1, 40),
  ]);

  const articles = regionalNews.articles;
  const { stats } = regionalNews;
  const coverageLabel = stats.coveredCountries
    .map((code) => COUNTRIES.find((c) => c.code === code)?.name || code.toUpperCase())
    .join(", ");

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Hero Header */}
      <div className="bg-gradient-to-r from-primary to-primary-dark text-white py-16">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3 mb-4">
            <Globe2 className="h-8 w-8" />
            <span className="text-white/80 text-lg">Region</span>
          </div>
          <h1 className="text-4xl md:text-5xl font-serif font-bold mb-4">
            {regionData.name} News
          </h1>
          <p className="text-xl text-white/80 max-w-2xl">
            Breaking headlines and stories from {regionData.countries.length}{" "}
            countries across {regionData.name}
          </p>

          {/* Country Quick Links */}
          <div className="mt-8 flex flex-wrap gap-2">
            {regionCountries.slice(0, 10).map((country) => (
              <Link
                key={country.code}
                href={`/country/${country.code}`}
                className="inline-flex items-center gap-1 px-3 py-1.5 bg-white/10 hover:bg-white/20 rounded-full text-sm transition-colors"
              >
                <span>{country.flag}</span>
                <span>{country.name}</span>
              </Link>
            ))}
            {regionCountries.length > 10 && (
              <span className="px-3 py-1.5 text-white/60 text-sm">
                +{regionCountries.length - 10} more
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid lg:grid-cols-4 gap-8">
          {/* Main Content */}
          <div className="lg:col-span-3">
            {/* Top Stories */}
            <section className="mb-12">
              <div className="flex items-center gap-2 mb-6">
                <Newspaper className="h-6 w-6 text-primary" />
                <h2 className="text-2xl font-bold text-gray-900 dark:text-white">
                  Top Stories from {regionData.name}
                </h2>
              </div>
              <NewsGrid
                articles={articles.slice(0, 12)}
                variant="default"
                columns={3}
                showFeatured={true}
              />
            </section>

            {/* More News */}
            <section>
              <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-6">
                More Headlines
              </h2>
              {articles.length > 12 ? (
                <NewsGrid
                  articles={articles.slice(12)}
                  variant="horizontal"
                  columns={1}
                />
              ) : null}
              {articles.length === 0 && (
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {stats.failedSources > 0
                    ? `No stories could be loaded right now: ${stats.failedSources} of ${stats.attemptedSources} sources failed.`
                    : "No stories are available from the sources in this region right now."}
                </p>
              )}
            </section>
          </div>

          {/* Sidebar */}
          <aside className="space-y-6">
            {/* Countries in Region */}
            <div className="bg-white dark:bg-gray-800 rounded-xl p-6 shadow">
              <div className="flex items-center gap-2 mb-4">
                <MapPin className="h-5 w-5 text-primary" />
                <h3 className="font-bold text-gray-900 dark:text-white">
                  Countries ({regionCountries.length})
                </h3>
              </div>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {regionCountries.map((country) => (
                  <Link
                    key={country.code}
                    href={`/country/${country.code}`}
                    className="flex items-center justify-between p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  >
                    <span className="flex items-center gap-2">
                      <span className="text-lg">{country.flag}</span>
                      <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
                        {country.name}
                      </span>
                    </span>
                    <span className="text-gray-400">→</span>
                  </Link>
                ))}
              </div>
            </div>

            {/* Regional Stats */}
            <div className="bg-gradient-to-br from-accent to-accent-dark rounded-xl p-6 text-white">
              <h3 className="font-bold mb-4">Regional Overview</h3>
              <div className="space-y-3">
                <div className="flex justify-between">
                  <span className="text-white/80">Countries</span>
                  <span className="font-semibold">
                    {regionCountries.length}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-white/80">News Sources</span>
                  <span className="font-semibold">
                    {stats.successfulSources}/{stats.attemptedSources} online
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-white/80">Failed Sources</span>
                  <span className="font-semibold">{stats.failedSources}</span>
                </div>
              </div>
            </div>

            {/* Fetch Coverage */}
            <div className="bg-white dark:bg-gray-800 rounded-xl p-6 shadow">
              <h3 className="font-bold text-gray-900 dark:text-white mb-4">
                This Fetch
              </h3>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Attempted</span>
                  <span className="font-medium text-gray-900 dark:text-white">
                    {stats.attemptedSources}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Succeeded</span>
                  <span className="font-medium text-gray-900 dark:text-white">
                    {stats.successfulSources}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-500 dark:text-gray-400">Failed</span>
                  <span className="font-medium text-gray-900 dark:text-white">
                    {stats.failedSources}
                  </span>
                </div>
                <div className="pt-2 border-t border-gray-100 dark:border-gray-700">
                  <div className="text-gray-500 dark:text-gray-400 mb-1">
                    Countries with stories ({stats.coveredCountries.length})
                  </div>
                  <p className="text-gray-900 dark:text-white break-words">
                    {coverageLabel || "None"}
                  </p>
                </div>
                {stats.failures.length > 0 && (
                  <details className="pt-2 border-t border-gray-100 dark:border-gray-700">
                    <summary className="cursor-pointer text-gray-500 dark:text-gray-400">
                      Failed sources ({stats.failures.length})
                    </summary>
                    <ul className="mt-2 space-y-1">
                      {stats.failures.map((failure) => (
                        <li
                          key={failure.sourceId}
                          className="text-xs text-gray-600 dark:text-gray-300 break-words"
                        >
                          <span className="font-medium">{failure.sourceId}</span>:{" "}
                          {failure.error}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </div>

            {/* Other Regions */}
            <div className="bg-white dark:bg-gray-800 rounded-xl p-6 shadow">
              <h3 className="font-bold text-gray-900 dark:text-white mb-4">
                Other Regions
              </h3>
              <div className="space-y-2">
                {REGIONS.filter((r) => r.id !== region).map((r) => (
                  <Link
                    key={r.id}
                    href={`/region/${r.id}`}
                    className="flex items-center gap-2 p-2 hover:bg-gray-50 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  >
                    <Globe2 className="h-4 w-4 text-gray-400" />
                    <span className="text-sm text-gray-700 dark:text-gray-200">
                      {r.name}
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
