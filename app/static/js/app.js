document.querySelectorAll("[data-share-button]").forEach((shareButton) => {
  shareButton.addEventListener("click", async () => {
    const feedback = shareButton
      .closest(".card-actions, .bible-chapter-navigation")
      .querySelector("[data-share-feedback]");
    const verses = [...document.querySelectorAll(".verse-text, .bible-verse-text")]
      .map((verse) => {
        const text = verse.cloneNode(true);
        text.querySelector("sup")?.remove();
        return text.textContent.trim();
      })
      .filter(Boolean);
    const reference = shareButton.dataset.shareReference;
    const translation = shareButton.dataset.shareTranslation;
    const attribution = shareButton.dataset.shareAttribution;
    const shareText = [
      verses.join("\n"),
      `${reference} · ${translation}`,
      attribution,
    ].join("\n\n");

    if (navigator.share) {
      try {
        await navigator.share({
          title: "Palavra do Dia",
          text: shareText,
          url: window.location.href,
        });
        feedback.textContent = "Leitura compartilhada.";
      } catch (error) {
        if (error.name !== "AbortError") {
          feedback.textContent = "Não foi possível compartilhar agora.";
        }
      }
      return;
    }

    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(shareText);
        feedback.textContent = "Referência e leitura copiadas.";
      } catch {
        feedback.textContent = "Não foi possível copiar a leitura neste navegador.";
      }
      return;
    }

    feedback.textContent = "Este navegador não oferece compartilhamento ou cópia.";
  });
});

const bookSearch = document.querySelector("[data-bible-book-search]");

if (bookSearch) {
  const books = [...document.querySelectorAll("[data-bible-book]")];
  const sections = [...document.querySelectorAll("[data-testament-section]")];
  const feedback = document.querySelector("[data-bible-search-feedback]");
  const emptyState = document.querySelector("[data-bible-search-empty]");
  const normalize = (value) => value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-BR")
    .trim();

  const updateBookSearch = () => {
    const query = normalize(bookSearch.value);
    let visibleCount = 0;

    books.forEach((book) => {
      const matches = normalize(book.dataset.bookName).includes(query);
      book.hidden = !matches;
      if (matches) visibleCount += 1;
    });

    sections.forEach((section) => {
      section.hidden = !section.querySelector("[data-bible-book]:not([hidden])");
    });
    emptyState.hidden = visibleCount > 0;
    feedback.textContent = query
      ? `${visibleCount} ${visibleCount === 1 ? "livro encontrado" : "livros encontrados"}`
      : `${visibleCount} livros disponíveis`;
  };

  bookSearch.addEventListener("input", updateBookSearch);
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "/"
      && !event.ctrlKey
      && !event.metaKey
      && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
    ) {
      event.preventDefault();
      bookSearch.focus();
    }
  });
}

const readerText = document.querySelector("[data-reader-text]");

document.querySelectorAll("[data-bible-book-jump], [data-bible-chapter-jump]").forEach((select) => {
  select.addEventListener("change", () => {
    if (select.value) window.location.assign(select.value);
  });
});

document.querySelectorAll("[data-range-output]").forEach((input) => {
  const output = document.getElementById(input.dataset.rangeOutput);
  const updateOutput = () => {
    output.value = `${input.value}%`;
    output.textContent = `${input.value}%`;
  };
  input.addEventListener("input", updateOutput);
  updateOutput();
});

document.querySelectorAll("[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

if (readerText) {
  const fontLabel = document.querySelector("[data-reader-font-label]");
  const fontButtons = document.querySelectorAll("[data-reader-font]");
  let fontScale = Number(document.body.dataset.fontScale || 100) / 100;

  const updateFontScale = () => {
    readerText.style.setProperty("--reader-font-scale", fontScale.toFixed(1));
    fontLabel.textContent = `${Math.round(fontScale * 100)}%`;
  };
  updateFontScale();

  fontButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const adjustment = button.dataset.readerFont === "increase" ? 0.1 : -0.1;
      fontScale = Math.min(1.5, Math.max(0.8, Math.round((fontScale + adjustment) * 10) / 10));
      updateFontScale();
    });
  });

  const focusButton = document.querySelector("[data-reader-focus]");
  const focusLabel = document.querySelector("[data-reader-focus-label]");
  const setFocusMode = (enabled) => {
    document.body.classList.toggle("bible-focus-mode", enabled);
    focusButton.setAttribute("aria-pressed", String(enabled));
    focusLabel.textContent = enabled ? "Sair do modo foco" : "Foco de leitura";
  };

  focusButton.addEventListener("click", () => {
    setFocusMode(focusButton.getAttribute("aria-pressed") !== "true");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("bible-focus-mode")) {
      setFocusMode(false);
    }
  });
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Try the legacy selection fallback when browser clipboard permissions deny access.
    }
  }

  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.append(field);
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  } finally {
    field.remove();
  }
  return copied;
}

document.querySelectorAll("[data-copy-prayer]").forEach((button) => {
  button.addEventListener("click", async () => {
    const prayer = button.closest(".prayer-card").querySelector("[data-prayer-text]");
    const feedback = button
      .closest(".prayer-actions")
      .querySelector("[data-prayer-feedback]");
    const copied = await copyText(prayer.textContent.trim());
    feedback.textContent = copied
      ? "Oração copiada."
      : "Não foi possível copiar a oração neste navegador.";
  });
});

const cardBuilder = document.querySelector("[data-card-builder]");

if (cardBuilder) {
  const content = cardBuilder.querySelector("[data-card-content]");
  const canvas = cardBuilder.querySelector("[data-verse-card-canvas]");
  const context = canvas?.getContext("2d");
  const options = Object.fromEntries(
    [...cardBuilder.querySelectorAll("[data-card-option]")].map((select) => [
      select.dataset.cardOption,
      select,
    ]),
  );

  const palettes = {
    aurora: ["#10253d", "#173e5b"],
    noite: ["#081321", "#172b43"],
    papel: ["#f5efe2", "#e7d8bd"],
  };
  const accents = {
    gold: "#d9b86c",
    blue: "#8bb8d8",
    rose: "#d9a7a4",
  };

  const drawCard = () => {
    if (!content || !context) return;
    const model = options.model.value;
    const [top, bottom] = palettes[model];
    const accent = accents[options.accent.value];
    const darkText = model === "papel";
    const gradient = context.createLinearGradient(0, 0, canvas.width, canvas.height);
    gradient.addColorStop(0, top);
    gradient.addColorStop(1, bottom);
    context.fillStyle = gradient;
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = accent;
    context.globalAlpha = 0.18;
    context.beginPath();
    context.arc(1040, 140, 230, 0, Math.PI * 2);
    context.fill();
    context.globalAlpha = 1;

    const align = options.align.value;
    const x = align === "left" ? 110 : canvas.width / 2;
    const maxWidth = 980;
    const font = options.font.value === "serif"
      ? '52px Georgia, "Times New Roman", serif'
      : '52px "Segoe UI", Arial, sans-serif';
    context.font = font;
    context.fillStyle = darkText ? "#273342" : "#f7f4ed";
    context.textAlign = align;
    const words = content.dataset.verseText.split(/\s+/);
    const lines = [];
    let line = "";
    words.forEach((word) => {
      const candidate = line ? `${line} ${word}` : word;
      if (context.measureText(candidate).width > maxWidth && line) {
        lines.push(line);
        line = word;
      } else {
        line = candidate;
      }
    });
    if (line) lines.push(line);
    const lineHeight = 75;
    const startY = Math.max(310, (canvas.height - lines.length * lineHeight) / 2);
    lines.forEach((value, index) => {
      context.fillText(value, x, startY + index * lineHeight);
    });
    context.font = 'bold 30px "Segoe UI", Arial, sans-serif';
    context.fillStyle = accent;
    context.fillText(content.dataset.reference, x, startY + lines.length * lineHeight + 70);
    context.font = '22px "Segoe UI", Arial, sans-serif';
    context.fillStyle = darkText ? "#526273" : "#c8d1dc";
    context.textAlign = "left";
    context.fillText(`${content.dataset.translation} · BLIVRE/porbr2018`, 110, 1080);
    context.font = '17px "Segoe UI", Arial, sans-serif';
    context.fillText(content.dataset.attribution, 110, 1120, 980);
  };

  Object.values(options).forEach((select) => select.addEventListener("change", drawCard));
  drawCard();

  cardBuilder.querySelector("[data-card-download]")?.addEventListener("click", () => {
    const link = document.createElement("a");
    link.download = "palavra-do-dia-cartao.png";
    link.href = canvas.toDataURL("image/png");
    link.click();
  });

  cardBuilder.querySelector("[data-card-share]")?.addEventListener("click", async () => {
    if (!navigator.share || !canvas.toBlob) {
      const link = document.createElement("a");
      link.download = "palavra-do-dia-cartao.png";
      link.href = canvas.toDataURL("image/png");
      link.click();
      return;
    }
    canvas.toBlob(async (blob) => {
      if (!blob) return;
      try {
        await navigator.share({
          title: "Palavra do Dia",
          text: `${content.dataset.reference} · BLIVRE/porbr2018`,
          files: [new File([blob], "palavra-do-dia-cartao.png", { type: "image/png" })],
        });
      } catch (error) {
        if (error.name !== "AbortError") {
          window.alert("Não foi possível compartilhar o cartão neste navegador.");
        }
      }
    }, "image/png");
  });
}

const spaceMenu = document.querySelector(".nav-more");

if (spaceMenu) {
  const summary = spaceMenu.querySelector("summary");
  const menuItems = [...spaceMenu.querySelectorAll('[role="menuitem"]')];

  const setExpanded = (expanded) => {
    summary.setAttribute("aria-expanded", String(expanded));
  };

  spaceMenu.addEventListener("toggle", () => {
    const expanded = spaceMenu.open;
    setExpanded(expanded);
    if (expanded) {
      window.setTimeout(() => menuItems[0]?.focus(), 0);
    }
  });

  document.addEventListener("click", (event) => {
    if (spaceMenu.open && !spaceMenu.contains(event.target)) {
      spaceMenu.open = false;
      setExpanded(false);
    }
  });

  summary.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!spaceMenu.open) {
        spaceMenu.open = true;
      } else {
        menuItems[0]?.focus();
      }
    }
  });

  spaceMenu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      spaceMenu.open = false;
      setExpanded(false);
      summary.focus();
      return;
    }

    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const currentIndex = menuItems.indexOf(document.activeElement);
    if (currentIndex < 0) return;
    event.preventDefault();
    const offset = event.key === "ArrowDown" ? 1 : -1;
    menuItems[(currentIndex + offset + menuItems.length) % menuItems.length]?.focus();
  });
}
