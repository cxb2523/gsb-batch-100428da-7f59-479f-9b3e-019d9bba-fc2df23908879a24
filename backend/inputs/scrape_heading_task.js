/**
 * @typedef {import('../../frontend/node_modules/botasaurus-controls/dist/index').Controls} Controls
 */

/**
 * @param {Controls} controls
 */
function getInput(controls) {
    controls
        // Render a Link Input
        .link('link', { isRequired: true, defaultValue: "https://www.omkar.cloud/" })
        // Render a Links Input for batch scraping (takes precedence over link when filled)
        .listOfLinks('links', { placeholder: "https://www.omkar.cloud/" })
}
