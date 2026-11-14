// Fixture page controller for Google Maps scraper tests.
//
// The page mimics the Maps result panel markup the scrapers target:
//   div[role="feed"] > div.Nv2PK.THOPZb.CpccDe cards containing the same
//   class names / aria-labels as real cards.
//
// Scenario is selected via the URL hash, e.g. fixture.html#normal:
//   normal      - multiple fully populated cards
//   missing     - cards with some fields absent (no rating, image, ...)
//   empty       - feed present but zero cards
//   early-bottom- feed never grows when scrolled (cards exist up front)
//   growing     - each scroll appends a batch of cards, then stops growing
//
// The growing scenario is what proves the stall detection: once no more
// batches are appended, two consecutive scrolls see identical heights and
// the scraper must stop before max_scrolls is exhausted.

(function () {
  const scenario = (location.hash || '#normal').slice(1);

  // Card data is also injected by tests through window.__FIXTURE_CARDS__;
  // these defaults keep the page usable when opened directly.
  const FULL_CARDS = [
    {
      name: 'Alpha Bistro',
      rating: '4.6',
      reviews: '1,234',
      category: 'Restaurant',
      services: ['Dine-in', 'Delivery'],
      image: 'https://lh3.googleusercontent.com/alpha',
      url: 'https://www.google.com/maps/place/Alpha',
    },
    {
      name: 'Beta Cafe',
      rating: '4.2',
      reviews: '56',
      category: 'Cafe',
      services: ['Takeout'],
      image: 'https://lh3.googleusercontent.com/beta',
      url: 'https://www.google.com/maps/place/Beta',
    },
    {
      name: 'Gamma Grill',
      rating: '3.9',
      reviews: '12,000',
      category: 'Bar & grill',
      services: [],
      image: 'https://lh3.googleusercontent.com/gamma',
      url: 'https://www.google.com/maps/place/Gamma',
    },
  ];

  // Every card drops at least one field so parsers must tolerate gaps.
  const MISSING_CARDS = [
    {
      name: 'No Rating Diner',
      rating: '',
      reviews: '89',
      category: 'Diner',
      services: ['Dine-in'],
      image: '',
      url: 'https://www.google.com/maps/place/NoRating',
    },
    {
      name: '',
      rating: '4.0',
      reviews: '',
      category: '',
      services: [],
      image: 'https://lh3.googleusercontent.com/noname',
      url: '',
    },
    {
      name: 'Bare Bones',
      rating: '',
      reviews: '',
      category: '',
      services: [],
      image: '',
      url: '',
    },
  ];

  const GROW_BATCHES = 4; // batches appended while scrolling
  const GROW_BATCH_SIZE = 2;

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function buildCard(data, index) {
    const card = el('div', 'Nv2PK THOPZb CpccDe');

    if (data.name) {
      const name = el('div', 'qBF1Pd fontHeadlineSmall');
      name.textContent = data.name;
      card.appendChild(name);
    }

    if (data.rating) {
      const ratingSpan = el('span');
      ratingSpan.setAttribute('aria-label', `Rated ${data.rating} out of 5 stars`);
      ratingSpan.textContent = data.rating;
      card.appendChild(ratingSpan);
    }

    if (data.reviews) {
      const reviewsSpan = el('span', 'UY7F9');
      reviewsSpan.textContent = `(${data.reviews})`;
      card.appendChild(reviewsSpan);
    }

    if (data.category) {
      const meta = el('div', 'W4Efsd');
      const categorySpan = el('span');
      categorySpan.textContent = data.category;
      meta.appendChild(categorySpan);
      card.appendChild(meta);
    }

    if (data.services && data.services.length) {
      const services = el('div', 'ah5Ghc');
      data.services.forEach((service) => {
        const span = el('span');
        span.textContent = service;
        services.appendChild(span);
      });
      card.appendChild(services);
    }

    if (data.image) {
      const img = el('img');
      img.src = data.image;
      img.alt = `photo ${index}`;
      card.appendChild(img);
    }

    if (data.url) {
      const link = el('a', 'hfpxzc');
      link.href = data.url;
      card.appendChild(link);
    }

    return card;
  }

  const feed = document.querySelector('div[role="feed"]');

  function render(cards) {
    cards.forEach((data, index) => feed.appendChild(buildCard(data, index)));
  }

  function makeGrowingCard(batch, offset) {
    const index = batch * GROW_BATCH_SIZE + offset;
    return {
      name: `Growing Place ${index + 1}`,
      rating: ((index % 5) + 30) / 10, // 3.0 .. 3.4
      reviews: `${(index + 1) * 100}`,
      category: 'Shop',
      services: offset === 0 ? ['Delivery'] : [],
      image: `https://lh3.googleusercontent.com/growing-${index}`,
      url: `https://www.google.com/maps/place/Growing${index}`,
    };
  }

  let batchesAppended = 0;

  function appendBatch() {
    if (batchesAppended >= GROW_BATCHES) return false;
    const cards = [];
    for (let offset = 0; offset < GROW_BATCH_SIZE; offset += 1) {
      cards.push(makeGrowingCard(batchesAppended, offset));
    }
    render(cards);
    batchesAppended += 1;
    return true;
  }

  // A scroll to the bottom triggers the next append, like Maps lazy
  // loading. This is only enabled for the explicit "growing" scenario so
  // static panels (normal, missing, early-bottom) keep a fixed card count.
  feed.addEventListener('scroll', () => {
    if (scenario !== 'growing') return;
    if (
      feed.scrollTop + feed.clientHeight >=
      feed.scrollHeight - 2
    ) {
      appendBatch();
    }
  });

  // Allow tests to force the card set instead of using the defaults.
  window.__setFixtureCards = function (cards) {
    feed.innerHTML = '';
    render(cards);
  };
  window.__appendBatch = appendBatch;
  window.__fixtureState = () => ({ batchesAppended });

  if (scenario === 'normal') {
    render(window.__FIXTURE_CARDS__ || FULL_CARDS);
  } else if (scenario === 'missing') {
    render(MISSING_CARDS);
  } else if (scenario === 'empty') {
    // Intentionally no cards.
  } else if (scenario === 'early-bottom') {
    // Fixed set of cards; scrolling never changes the height.
    render(FULL_CARDS);
  } else if (scenario === 'growing') {
    appendBatch();
  } else {
    render(FULL_CARDS);
  }
})();
