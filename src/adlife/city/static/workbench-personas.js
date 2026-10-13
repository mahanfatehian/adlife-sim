"use strict";

(() => {
  const copy = value => JSON.parse(JSON.stringify(value));
  const traits = [
    ["price_sensitivity", "Price sensitivity"], ["novelty_seeking", "Novelty seeking"],
    ["advertising_skepticism", "Ad skepticism"], ["mobile_recall_encoding", "Phone recall encoding"],
    ["roadside_recall_encoding", "Billboard recall encoding"], ["impulsivity", "Impulsivity"],
  ];
  const initial = [["brand_sentiment", "Initial sentiment"], ["recall_strength", "Initial recall"],
    ["purchase_intention", "Initial purchase-intention proxy"]];
  const mbti = ["", "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ"];
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const fieldId = path => `persona-${path.replace("traits.", "trait-").replace("initial_state.", "initial-").replaceAll("_", "-")}`;

  class PersonaEditor {
    static nextPersonaId(personas) {
      const used = new Set(personas.map(item => item.persona_id));
      for (let index = 1; index <= 999; index += 1) {
        const id = `persona-${String(index).padStart(3, "0")}`;
        if (!used.has(id)) return id;
      }
      throw new Error("No fictional persona identifier is available.");
    }

    constructor(root, catalog, count, onChange, onReveal) {
      if (catalog.schema_version !== 1 || !Array.isArray(catalog.templates) ||
          catalog.templates.length !== 4 || catalog.templates.some((item, index) =>
            item.persona_id !== `persona-00${index + 1}` || item.fictional !== true ||
            typeof item.display_name !== "string" || !Array.isArray(item.interests) ||
            !item.traits || !item.initial_state)) {
        throw new Error("The fictional persona catalog is incompatible.");
      }
      this.root = root;
      root.editor = this;
      this.templates = copy(catalog.templates);
      this.onChange = onChange;
      this.onReveal = onReveal;
      this.disclosures = new Map();
      this.reset(count);
    }

    reset(count) {
      this.personas = [copy(this.templates[0])];
      this.personas[0].count = count;
      this.selected = this.personas[0].persona_id;
      this.disclosures.clear();
      this.render();
    }

    total() {
      return this.personas.every(p => Number.isInteger(p.count) && p.count >= 1 && p.count <= 30)
        ? this.personas.reduce((total, p) => total + p.count, 0) : null;
    }

    add(templateId) {
      if (this.personas.length >= 30 || this.total() === null || this.total() >= 30) return false;
      const template = this.templates.find(p => p.persona_id === templateId);
      if (!template) return false;
      const persona = copy(template);
      persona.persona_id = PersonaEditor.nextPersonaId(this.personas);
      persona.count = 1;
      this.personas.push(persona);
      this.selected = persona.persona_id;
      this.changed(true);
      return true;
    }

    duplicate() {
      if (this.personas.length >= 30 || this.total() === null || this.total() >= 30) return false;
      const persona = copy(this.personas.find(p => p.persona_id === this.selected));
      persona.persona_id = PersonaEditor.nextPersonaId(this.personas);
      persona.count = 1;
      this.personas.push(persona);
      this.selected = persona.persona_id;
      this.changed(true);
      return true;
    }

    remove() {
      if (this.personas.length <= 1) return false;
      const index = this.personas.findIndex(p => p.persona_id === this.selected);
      this.personas.splice(index, 1);
      this.selected = this.personas[Math.min(index, this.personas.length - 1)].persona_id;
      this.changed(true);
      return true;
    }

    edit(path, value) {
      const persona = this.personas.find(p => p.persona_id === this.selected);
      const allowed = ["count", "display_name", "age", "occupation", "background", "mbti", "interests",
        ...traits.map(([key]) => `traits.${key}`), ...initial.map(([key]) => `initial_state.${key}`)];
      if (!allowed.includes(path)) return false;
      const parts = path.split(".");
      if (parts.length === 2) persona[parts[0]][parts[1]] = copy(value);
      else persona[path] = copy(value);
      this.changed(false);
      return true;
    }

    changed(render) {
      if (render) {
        this.render();
        this.root.querySelector("#persona-display-name").focus();
      } else this.renderRoster();
      this.onChange();
    }

    serialize() {
      return { personas: copy(this.personas) };
    }

    issue(agentCount) {
      for (let index = 0; index < this.personas.length; index += 1) {
        const p = this.personas[index];
        if (!Number.isInteger(p.count) || p.count < 1 || p.count > 30) {
          return { path: `population.personas.${index}.count`, message: "Enter a whole count from 1 through 30." };
        }
      }
      if (this.total() !== agentCount) return { path: "population", message: "Audience counts must match run agent count. Review the audience or use its total." };
      return null;
    }

    clearErrors() {
      for (const node of this.root.querySelectorAll(".field-error")) node.textContent = "";
      for (const node of this.root.querySelectorAll('[aria-invalid="true"]')) node.removeAttribute("aria-invalid");
    }

    focusError(path, message = null) {
      const match = /^population\.personas\.(\d+)(?:\.(.+))?$/.exec(path);
      if (!match) return false;
      const p = this.personas[Number(match[1])];
      if (!p) return false;
      const allowed = ["count", "display_name", "age", "occupation", "background", "mbti", "interests",
        ...traits.map(([key]) => `traits.${key}`), ...initial.map(([key]) => `initial_state.${key}`)];
      const field = /^interests\.\d+$/.test(match[2] || "") ? "interests" : match[2];
      const aggregate = !allowed.includes(field);
      this.onReveal();
      if (this.selected !== p.persona_id) {
        this.selected = p.persona_id;
        this.render();
      }
      const control = aggregate ? this.root.querySelector("#persona-detail") :
        this.root.querySelector(`#${fieldId(field)}`);
      const details = control.closest("details");
      if (details) details.open = true;
      if (aggregate) this.root.querySelector("#persona-description").open = true;
      const error = this.root.querySelector(aggregate ? "#persona-error" : `#${control.id}-error`);
      error.textContent = message || (aggregate ?
        "The server refused this persona. Review its fields and size." :
        "The server refused this field. Review its value.");
      control.setAttribute("aria-invalid", "true");
      control.focus();
      return true;
    }

    renderRoster() {
      const roster = this.root.querySelector("#persona-roster");
      // Keep existing roster controls mounted during typing, including keyboard focus.
      if (roster.children.length !== this.personas.length ||
          [...roster.children].some((node, i) => node.dataset.personaId !== this.personas[i].persona_id)) {
        roster.replaceChildren(...this.personas.map(p => {
          const button = element("button", undefined, "persona-row");
          button.type = "button";
          button.dataset.personaId = p.persona_id;
          button.addEventListener("click", () => {
            this.selected = p.persona_id;
            this.renderDetail();
            this.renderRoster();
          });
          return button;
        }));
      }
      [...roster.children].forEach((button, index) => {
        const p = this.personas[index];
        button.textContent = `${p.display_name} · ${p.persona_id} · ${p.count ?? "?"} people`;
        button.setAttribute("aria-pressed", String(p.persona_id === this.selected));
      });
      const full = this.personas.length >= 30 || this.total() === null || this.total() >= 30;
      this.root.querySelector("#persona-add").disabled = full;
      this.root.querySelector("#persona-duplicate").disabled = full;
      this.root.querySelector("#persona-remove").disabled = this.personas.length === 1;
      this.root.querySelector("#persona-total").textContent = `${this.personas.length} definitions · ${this.total() ?? "invalid"} people`;
    }

    field(parent, path, label, value, type = "text", min = null, max = null) {
      const wrapper = element("div", undefined, "field");
      const id = fieldId(path);
      const caption = element("label", label);
      caption.htmlFor = id;
      let control;
      if (type === "select") {
        control = element("select");
        for (const optionValue of mbti) {
          const option = element("option", optionValue || "No label");
          option.value = optionValue;
          control.append(option);
        }
      } else if (type === "textarea") control = element("textarea");
      else {
        control = element("input");
        control.type = type;
      }
      control.id = id;
      control.value = Array.isArray(value) ? value.join("\n") : value ?? "";
      if (type === "number") {
        control.min = String(min);
        control.max = String(max);
        control.step = path === "count" || path === "age" ? "1" : "any";
      }
      control.setAttribute("aria-describedby", `${id}-error`);
      control.addEventListener(type === "select" ? "change" : "input", () => {
        let next = control.value;
        if (path === "interests") next = next.split("\n").map(text => text.trim()).filter(Boolean);
        else if (type === "number") next = next === "" ? null : Number(next);
        else if (["occupation", "background", "mbti"].includes(path) && next === "") next = null;
        this.edit(path, next);
      });
      const error = element("p", "", "field-error");
      error.id = `${id}-error`;
      error.setAttribute("role", "status");
      wrapper.append(caption, control, error);
      parent.append(wrapper);
    }

    renderDetail() {
      const detail = this.root.querySelector("#persona-detail");
      detail.replaceChildren();
      detail.tabIndex = -1;
      detail.setAttribute("role", "group");
      detail.setAttribute("aria-label", "Selected fictional persona");
      detail.setAttribute("aria-describedby", "persona-error");
      detail.removeAttribute("aria-invalid");
      const error = element("p", "", "field-error");
      error.id = "persona-error";
      error.setAttribute("role", "status");
      detail.append(error);
      const p = this.personas.find(item => item.persona_id === this.selected);
      const identity = element("p", "Immutable persona ID: ", "section-note");
      const id = element("code", p.persona_id);
      id.id = "persona-id";
      identity.append(id);
      detail.append(identity);
      this.field(detail, "display_name", "Fictional display name (descriptive)", p.display_name);
      this.field(detail, "count", "People assigned to this persona (1–30)", p.count, "number", 1, 30);
      const description = element("details", undefined, "technical-details");
      description.id = "persona-description";
      description.open = this.disclosures.get(this.selected) || false;
      description.addEventListener("toggle", () => this.disclosures.set(p.persona_id, description.open));
      description.append(element("summary", "Fictional biography and MBTI · noncausal"));
      this.field(description, "age", "Fictional age (optional, 18–65; descriptive)", p.age, "number", 18, 65);
      this.field(description, "occupation", "Fictional occupation (optional; descriptive)", p.occupation);
      this.field(description, "background", "Fictional background (optional; descriptive)", p.background, "textarea");
      this.field(description, "mbti", "MBTI (optional; descriptive, noncausal)", p.mbti, "select");
      detail.append(description);
      this.field(detail, "interests", "Model interests · one per line (1–12)", p.interests, "textarea");
      detail.append(element("h3", "Explicit response inputs", "persona-input-heading"));
      const grid = element("div", undefined, "persona-values");
      for (const [key, label] of traits) this.field(grid, `traits.${key}`, `${label} (0–1)`, p.traits[key], "number", 0, 1);
      for (const [key, label] of initial) this.field(grid, `initial_state.${key}`, `${label} (${key === "brand_sentiment" ? "−1" : "0"}–1)`, p.initial_state[key], "number", key === "brand_sentiment" ? -1 : 0, 1);
      detail.append(grid, element("p", "Purchase intention is a model score, not a purchase probability or sales forecast.", "section-note"));
    }

    render() {
      this.root.replaceChildren();
      const toolbar = element("div", undefined, "persona-toolbar");
      const label = element("label", "Fictional starting point");
      label.htmlFor = "persona-template";
      const select = element("select");
      select.id = "persona-template";
      for (const p of this.templates) {
        const option = element("option", p.display_name);
        option.value = p.persona_id;
        select.append(option);
      }
      toolbar.append(label, select);
      for (const [key, title, action] of [["add", "Add persona", () => this.add(select.value)],
        ["duplicate", "Duplicate selected", () => this.duplicate()], ["remove", "Remove selected", () => this.remove()]]) {
        const button = element("button", title, "button quiet compact");
        button.id = `persona-${key}`;
        button.type = "button";
        button.addEventListener("click", action);
        toolbar.append(button);
      }
      const total = element("p", "", "section-note");
      total.id = "persona-total";
      total.setAttribute("role", "status");
      const roster = element("div", undefined, "persona-roster");
      roster.id = "persona-roster";
      roster.setAttribute("role", "group");
      roster.setAttribute("aria-label", "Fictional audience roster");
      const detail = element("div");
      detail.id = "persona-detail";
      this.root.append(toolbar, total, roster, detail);
      this.renderDetail();
      this.renderRoster();
    }
  }
  window.AdLifePersonaEditor = PersonaEditor;
})();
