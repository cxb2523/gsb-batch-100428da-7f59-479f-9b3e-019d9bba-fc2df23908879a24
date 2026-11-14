import http from "http";
import { JSDOM } from "jsdom";

// Keep `http.IncomingMessage` functionality (stream methods such as
// `destroy`, socket handling, etc.) while exposing the jsdom-based DOM
// helpers. Replacing the whole prototype chain with a plain object would
// break modern Node.js stream internals:
const response = Object.create(http.IncomingMessage.prototype, {
  jsdom: {
    get(): JSDOM {
      return new JSDOM(this.body);
    }
  },

  window: {
    get(): JSDOM {
      return this.jsdom.window;
    }
  },

  document: {
    get(): JSDOM {
      return this.window.document;
    }
  }
});

export default response;
