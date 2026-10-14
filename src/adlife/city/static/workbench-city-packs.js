"use strict";

(() => {
  const UPLOAD_PATH = "/api/catalog/cities/upload";
  const MAX_UPLOAD_BYTES = 4_194_304;
  const MAX_RESPONSE_BYTES = 1_048_576;

  const boundedText = (value, maximum = 500) =>
    typeof value === "string" && value.length > 0 && value.length <= maximum;

  class CityPackUploadError extends Error {}

  function responseMessage(documentValue) {
    if (
      documentValue !== null &&
      typeof documentValue === "object" &&
      !Array.isArray(documentValue) &&
      documentValue.error !== null &&
      typeof documentValue.error === "object" &&
      !Array.isArray(documentValue.error) &&
      boundedText(documentValue.error.message)
    ) {
      return documentValue.error.message;
    }
    return "The city pack could not be registered.";
  }

  async function parseResponse(response) {
    if (response.redirected) {
      throw new CityPackUploadError("The city pack request was redirected and refused.");
    }
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.toLowerCase().startsWith("application/json")) {
      throw new CityPackUploadError("The city pack service returned an unexpected response.");
    }
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > MAX_RESPONSE_BYTES) {
      throw new CityPackUploadError("The city pack response exceeded its browser limit.");
    }
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (_error) {
      throw new CityPackUploadError("The city pack service returned invalid text.");
    }
    let documentValue;
    try {
      documentValue = JSON.parse(text);
    } catch (_error) {
      throw new CityPackUploadError("The city pack service returned malformed JSON.");
    }
    if (!response.ok) {
      throw new CityPackUploadError(responseMessage(documentValue));
    }
    return documentValue;
  }

  async function postCityPack(bytes, csrfToken) {
    if (!boundedText(csrfToken, 256)) {
      throw new CityPackUploadError("The local security token is unavailable. Reload the page.");
    }
    let response;
    try {
      response = await fetch(UPLOAD_PATH, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-AdLife-CSRF": csrfToken,
        },
        body: bytes,
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
      });
    } catch (_error) {
      throw new CityPackUploadError(
        "The city pack service did not respond. Check that the workbench is still running.",
      );
    }
    return parseResponse(response);
  }

  class CityPackUploader {
    constructor({ intake, input, choose, status, error, csrfToken, validateRow, confirm }) {
      this.intake = intake;
      this.input = input;
      this.choose = choose;
      this.status = status;
      this.error = error;
      this.csrfToken = csrfToken;
      this.validateRow = validateRow;
      this.confirm = confirm;
      this.generation = 0;

      choose.addEventListener("click", () => input.click());
      input.addEventListener("input", event => event.stopPropagation());
      input.addEventListener("change", event => {
        event.stopPropagation();
        const file = input.files && input.files.length === 1 ? input.files.item(0) : null;
        input.value = "";
        if (file !== null) {
          void this.register(file);
        }
      });
    }

    update(status, error, busy) {
      this.status.textContent = status;
      this.error.textContent = error;
      this.intake.setAttribute("aria-busy", busy ? "true" : "false");
    }

    supersede() {
      this.generation += 1;
      this.update("", "", false);
    }

    async register(file) {
      const generation = ++this.generation;
      this.input.value = "";
      this.update("Reading city pack\u2026", "", true);
      try {
        if (!Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_UPLOAD_BYTES) {
          throw new CityPackUploadError("Choose a city pack no larger than 4 MiB.");
        }
        const bytes = await file.arrayBuffer();
        if (generation !== this.generation) return;
        if (bytes.byteLength > MAX_UPLOAD_BYTES) {
          throw new CityPackUploadError("Choose a city pack no larger than 4 MiB.");
        }
        this.update("Registering city pack\u2026", "", true);
        const documentValue = await postCityPack(bytes, this.csrfToken);
        if (generation !== this.generation) return;
        const row = this.validateRow(documentValue);
        const confirmed = await this.confirm(row);
        if (generation !== this.generation) return;
        if (confirmed !== true) return;
        this.update("City pack ready.", "", false);
      } catch (caught) {
        if (generation !== this.generation) return;
        const message = caught instanceof Error && boundedText(caught.message)
          ? caught.message
          : "The city pack could not be registered.";
        this.update("", message, false);
        this.choose.focus();
      }
    }
  }

  window.AdLifeCityPackUploader = CityPackUploader;
})();
