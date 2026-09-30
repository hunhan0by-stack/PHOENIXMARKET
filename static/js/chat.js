// PHOENIXMARKET Support Chat
// Frontend-only chat widget with FAQ fallback and API-ready abstraction.

(function () {
  const root = document.getElementById("pm-chat-root");
  if (!root) return;

  const locale = document.body.getAttribute("data-locale") === "tr" ? "tr" : "en";
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrfToken = csrfMeta ? csrfMeta.getAttribute("content") : "";

  // ---------------------------------------------------------------------------
  // UI strings
  // ---------------------------------------------------------------------------
  const STRINGS = {
    en: {
      title: "Support",
      subtitle: "PHOENIXMARKET Assistant",
      online: "Online",
      openLabel: "Open PHOENIXMARKET support chat",
      closeLabel: "Close support chat",
      placeholder: "Ask about sign-in, orders, checkout...",
      typing: "Assistant is typing…",
      welcome:
        "Hi, I’m the PHOENIXMARKET assistant. I can help you with sign-in, cart, checkout, and general questions about how the marketplace works.",
      error:
        "Sorry, something went wrong while generating a response. Please try again or contact support via email/Discord.",
      quickReplies: [
        "How do I sign in?",
        "How do I place an order?",
        "Where is my cart?",
        "How do I contact support?",
      ],
    },
    tr: {
      title: "Destek",
      subtitle: "PHOENIXMARKET Asistanı",
      online: "Çevrimiçi",
      openLabel: "PHOENIXMARKET destek sohbetini aç",
      closeLabel: "Destek sohbetini kapat",
      placeholder: "Giriş, sipariş, ödeme hakkında sorun...",
      typing: "Asistan yazıyor…",
      welcome:
        "Merhaba, ben PHOENIXMARKET asistanıyım. Giriş, sepet, ödeme ve pazaryerinin işleyişiyle ilgili sorularınızda yardımcı olabilirim.",
      error:
        "Üzgünüz, yanıt oluşturulurken bir sorun oluştu. Lütfen tekrar deneyin veya e-posta/Discord üzerinden destek ekibine ulaşın.",
      quickReplies: [
        "Nasıl giriş yaparım?",
        "Nasıl sipariş veririm?",
        "Sepetim nerede?",
        "Destekle nasıl iletişime geçerim?",
      ],
    },
  };
  const S = STRINGS[locale];

  // Lowercase that also folds Turkish dotted capital I ("İ" -> "i").
  function normalize(text) {
    return (text || "").toLowerCase().replace(/̇/g, "");
  }

  // ---------------------------------------------------------------------------
  // FAQ fallback engine
  // ---------------------------------------------------------------------------
  const faqSupportEngine = (() => {
    const entries = [
      {
        keywords: ["sign in", "login", "log in", "giriş", "oturum"],
        reply: {
          en: "To sign in, use the “Sign In” link in the top navigation (or the menu on mobile) and enter your email and password. No account yet? Choose “Sign Up” to create one.",
          tr: "Giriş yapmak için üst menüdeki (mobilde menüdeki) “Giriş Yap” bağlantısını kullanın ve e-posta ile şifrenizi girin. Hesabınız yoksa “Kayıt Ol” ile yeni bir hesap oluşturabilirsiniz.",
        },
      },
      {
        keywords: ["sign up", "register", "kayıt", "üye"],
        reply: {
          en: "Create an account on the “Sign Up” page with your email and a password of at least 8 characters. Orders you place while signed in appear under Account → Order history.",
          tr: "“Kayıt Ol” sayfasından e-posta adresiniz ve en az 8 karakterli bir şifreyle hesap oluşturabilirsiniz. Giriş yapmışken verdiğiniz siparişler Hesap → Sipariş geçmişi bölümünde görünür.",
        },
      },
      {
        keywords: ["forgot", "reset", "password", "şifre", "parola"],
        reply: {
          en: "Forgot your password? Use “Forgot password” on the sign-in page and we’ll email you a link to choose a new one (valid for 1 hour). When signed in, you can change your password on the Account page.",
          tr: "Şifrenizi mi unuttunuz? Giriş sayfasındaki “Şifremi unuttum” bağlantısını kullanın; yeni şifre belirlemeniz için size bir bağlantı gönderelim (1 saat geçerli). Giriş yapmışken şifrenizi Hesap sayfasından değiştirebilirsiniz.",
        },
      },
      {
        keywords: ["track", "status", "where is my order", "takip", "durum", "siparişim nerede"],
        reply: {
          en: "Use the private tracking link shown after checkout to check your order status at any time. If you were signed in, the order also appears under Account → Order history.",
          tr: "Sipariş durumunuzu, ödeme sonrasında gösterilen özel takip bağlantısıyla dilediğiniz zaman kontrol edebilirsiniz. Giriş yapmışken sipariş verdiyseniz sipariş Hesap → Sipariş geçmişi bölümünde de görünür.",
        },
      },
      {
        keywords: ["cart", "sepet"],
        reply: {
          en: "Your cart is available from the “Cart” link in the navigation. Use “Add to cart” on any product, then adjust quantities in the cart. Quantities are limited by available stock.",
          tr: "Sepetinize menüdeki “Sepet” bağlantısından ulaşabilirsiniz. Ürünlerdeki “Sepete ekle” butonunu kullanın, ardından adetleri sepetten düzenleyin. Adetler mevcut stokla sınırlıdır.",
        },
      },
      {
        keywords: ["stock", "out of", "stok", "tükendi"],
        reply: {
          en: "Listings marked “Out of stock” can’t be added to the cart right now. Please check back later or contact support.",
          tr: "“Tükendi” olarak işaretlenen ilanlar şu anda sepete eklenemez. Lütfen daha sonra tekrar bakın veya destek ekibine ulaşın.",
        },
      },
      {
        keywords: ["checkout", "order", "buy", "purchase", "sipariş", "satın", "ödeme"],
        reply: {
          en: "Open your cart and click “Proceed to checkout”, fill in your name and email, and confirm. You’ll get a private tracking link and then complete payment on itemsatis.",
          tr: "Sepetinizi açıp “Ödemeye geç”e tıklayın, adınızı ve e-postanızı girip onaylayın. Size özel bir takip bağlantısı verilir, ardından ödemeyi itemsatis üzerinden tamamlarsınız.",
        },
      },
      {
        keywords: ["account", "profile", "hesap", "profil"],
        reply: {
          en: "Your Account page shows your profile and order history, and lets you change your password. You need to be signed in to open it.",
          tr: "Hesap sayfanızda profilinizi ve sipariş geçmişinizi görebilir, şifrenizi değiştirebilirsiniz. Bu sayfayı açmak için giriş yapmanız gerekir.",
        },
      },
      {
        keywords: ["contact", "support", "help", "destek", "iletişim", "yardım"],
        reply: {
          en: "You can reach PHOENIXMARKET support from the Contact page: send us a message with the form (add your order number if you have one) and we’ll reply by email. Email and Discord work too.",
          tr: "PHOENIXMARKET destek ekibine İletişim sayfasından ulaşabilirsiniz: formla bize mesaj gönderin (varsa sipariş numaranızı ekleyin), size e-posta ile dönelim. E-posta ve Discord da kullanılabilir.",
        },
      },
    ];

    const fallback = {
      en: "I’m not fully sure about that yet. For detailed or account-specific questions, please contact support via email or Discord on the Contact page, and we’ll help you directly.",
      tr: "Bu konuda henüz emin değilim. Ayrıntılı veya hesabınıza özel sorular için lütfen İletişim sayfasındaki e-posta veya Discord üzerinden destek ekibine ulaşın; size doğrudan yardımcı olalım.",
    };

    function getAnswer(message) {
      const text = normalize(message);
      for (const entry of entries) {
        if (entry.keywords.some((k) => text.includes(normalize(k)))) {
          return entry.reply[locale];
        }
      }
      return fallback[locale];
    }

    return { getAnswer };
  })();

  // ---------------------------------------------------------------------------
  // Chat service abstraction (ready for real AI API)
  // ---------------------------------------------------------------------------
  const supportChatService = (() => {
    const apiEnabledAttr = document.body.getAttribute("data-chat-api-enabled");
    const apiEnabled = apiEnabledAttr === "true";

    async function sendMessage(userMessage) {
      if (apiEnabled) {
        try {
          const resp = await fetch("/api/chat", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken,
            },
            body: JSON.stringify({ message: userMessage }),
          });

          const data = await resp.json().catch(() => null);
          if (resp.ok && data && data.answer) {
            return { text: data.answer, source: "api" };
          }
          // Rate limit / validation errors carry a user-facing message.
          if ((resp.status === 429 || resp.status === 400) && data && data.error) {
            return { text: data.error, source: "api-error" };
          }
          // If server reports AI not configured or any other non-OK status,
          // fall back to local FAQ below.
        } catch (e) {
          // Network or server error -> fallback.
          console.warn("AI chat request failed, using FAQ fallback.", e);
        }
      }

      // FAQ fallback (default when API is disabled or errors occur).
      const answer = faqSupportEngine.getAnswer(userMessage);
      return {
        text: answer,
        source: apiEnabled ? "ai+faq" : "faq-only",
      };
    }

    return { sendMessage, apiEnabled };
  })();

  // ---------------------------------------------------------------------------
  // Local storage for chat history
  // ---------------------------------------------------------------------------
  const STORAGE_KEY = "phoenixmarket_chat_history";

  function loadHistory() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function saveHistory(messages) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch {
      // ignore storage errors
    }
  }

  // ---------------------------------------------------------------------------
  // DOM creation helpers
  // ---------------------------------------------------------------------------
  function createEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text) el.textContent = text;
    return el;
  }

  // ---------------------------------------------------------------------------
  // Chat widget UI
  // ---------------------------------------------------------------------------
  let isOpen = false;
  let isSending = false;
  let messages = loadHistory();

  const launcher = createEl(
    "button",
    "fixed bottom-4 right-4 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-r from-brandRed to-brandOrange text-black shadow-xl shadow-black/50 focus:outline-none focus:ring-2 focus:ring-brandOrange/80 md:bottom-6 md:right-6"
  );
  launcher.setAttribute("aria-label", S.openLabel);
  launcher.innerHTML =
    '<span class="text-xl font-semibold select-none">?</span><span class="sr-only">Support chat</span>';

  const windowWrapper = createEl(
    "div",
    "fixed bottom-20 right-4 z-40 hidden w-[calc(100%-2rem)] max-w-sm md:bottom-24 md:right-6"
  );

  const windowPanel = createEl(
    "div",
    "flex h-96 flex-col rounded-2xl border border-slate-800 bg-slate-950/95 shadow-2xl shadow-black/70 backdrop-blur"
  );

  const header = createEl(
    "div",
    "flex items-center justify-between border-b border-slate-800 px-4 py-3"
  );
  const headerTitleWrap = createEl("div");
  const headerTitle = createEl(
    "p",
    "text-xs font-semibold uppercase tracking-[0.2em] text-brandOrange",
    S.title
  );
  const headerSub = createEl(
    "p",
    "mt-0.5 text-xs text-slate-300",
    S.subtitle
  );
  headerTitleWrap.appendChild(headerTitle);
  headerTitleWrap.appendChild(headerSub);

  const headerActions = createEl("div", "flex items-center gap-2");
  const headerStatus = createEl(
    "span",
    "inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300"
  );
  headerStatus.appendChild(
    createEl("span", "h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse")
  );
  headerStatus.appendChild(createEl("span", "", S.online));

  const headerClose = createEl(
    "button",
    "ml-1 text-xs text-slate-400 hover:text-slate-200"
  );
  headerClose.textContent = "×";
  headerClose.setAttribute("aria-label", S.closeLabel);

  headerActions.appendChild(headerStatus);
  headerActions.appendChild(headerClose);

  header.appendChild(headerTitleWrap);
  header.appendChild(headerActions);

  const messageContainer = createEl(
    "div",
    "flex-1 space-y-2 overflow-y-auto px-3 py-3 text-xs"
  );

  const quickReplies = createEl(
    "div",
    "flex flex-wrap gap-2 px-3 pb-2 text-[11px]"
  );

  S.quickReplies.forEach((label) => {
    const btn = createEl(
      "button",
      "rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-slate-300 hover:border-brandOrange hover:text-brandOrange"
    );
    btn.textContent = label;
    btn.addEventListener("click", () => {
      input.value = label;
      handleSend();
    });
    quickReplies.appendChild(btn);
  });

  const inputWrapper = createEl(
    "div",
    "border-t border-slate-800 px-3 py-2 text-xs"
  );
  const form = createEl("form", "flex items-end gap-2");
  const input = createEl(
    "textarea",
    "max-h-24 min-h-[40px] flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:border-brandOrange focus:outline-none",
    ""
  );
  input.setAttribute("rows", "1");
  input.setAttribute("maxlength", "1000");
  input.setAttribute("placeholder", S.placeholder);

  const sendBtn = createEl(
    "button",
    "inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-r from-brandRed to-brandOrange text-black disabled:opacity-60 disabled:cursor-not-allowed"
  );
  sendBtn.innerHTML =
    '<svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M4.5 19.5l15-7.5-15-7.5 3 7.5-3 7.5z" /></svg>';

  form.appendChild(input);
  form.appendChild(sendBtn);
  inputWrapper.appendChild(form);

  windowPanel.appendChild(header);
  windowPanel.appendChild(messageContainer);
  windowPanel.appendChild(quickReplies);
  windowPanel.appendChild(inputWrapper);
  windowWrapper.appendChild(windowPanel);

  root.appendChild(launcher);
  root.appendChild(windowWrapper);

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function renderMessages() {
    messageContainer.innerHTML = "";
    messages.forEach((m) => {
      const row = createEl(
        "div",
        "flex " + (m.role === "user" ? "justify-end" : "justify-start")
      );
      const bubble = createEl(
        "div",
        "max-w-[80%] whitespace-pre-line rounded-2xl px-3 py-2 " +
          (m.role === "user"
            ? "bg-brandRed text-black rounded-br-sm"
            : "bg-slate-800 text-slate-100 rounded-bl-sm")
      );
      bubble.textContent = m.text;
      row.appendChild(bubble);
      messageContainer.appendChild(row);
    });
    messageContainer.scrollTop = messageContainer.scrollHeight;
  }

  function addMessage(role, text) {
    messages.push({
      id: Date.now() + "-" + Math.random().toString(16).slice(2),
      role,
      text,
    });
    saveHistory(messages);
    renderMessages();
  }

  function addTypingIndicator() {
    const row = createEl("div", "flex justify-start", "");
    row.id = "pm-chat-typing";
    const bubble = createEl(
      "div",
      "inline-flex items-center gap-1 rounded-2xl bg-slate-800 px-3 py-2 text-[10px] text-slate-300 rounded-bl-sm"
    );
    bubble.appendChild(
      createEl("span", "h-1.5 w-1.5 rounded-full bg-slate-400 animate-pulse")
    );
    bubble.appendChild(createEl("span", "", S.typing));
    row.appendChild(bubble);
    messageContainer.appendChild(row);
    messageContainer.scrollTop = messageContainer.scrollHeight;
  }

  function removeTypingIndicator() {
    const el = document.getElementById("pm-chat-typing");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  // Initial welcome message
  if (!messages.length) {
    addMessage("assistant", S.welcome);
  } else {
    renderMessages();
  }

  // ---------------------------------------------------------------------------
  // Behavior
  // ---------------------------------------------------------------------------
  function openChat() {
    if (isOpen) return;
    isOpen = true;
    windowWrapper.classList.remove("hidden");
    launcher.setAttribute("aria-expanded", "true");
  }

  function closeChat() {
    if (!isOpen) return;
    isOpen = false;
    windowWrapper.classList.add("hidden");
    launcher.setAttribute("aria-expanded", "false");
  }

  launcher.addEventListener("click", () => {
    if (isOpen) closeChat();
    else openChat();
  });

  headerClose.addEventListener("click", closeChat);

  function handleSend() {
    const text = input.value.trim();
    if (!text || isSending) return;

    addMessage("user", text);
    input.value = "";
    isSending = true;
    sendBtn.disabled = true;
    addTypingIndicator();

    supportChatService
      .sendMessage(text)
      .then((res) => {
        removeTypingIndicator();
        addMessage("assistant", res.text);
      })
      .catch(() => {
        removeTypingIndicator();
        addMessage("assistant", S.error);
      })
      .finally(() => {
        isSending = false;
        sendBtn.disabled = false;
      });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSend();
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  });
})();
