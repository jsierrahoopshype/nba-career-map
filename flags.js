/* Shared country -> flag helper for index.html and teams.html.
 *
 * Maps each distinct country value stored in the dataset (names like "USA",
 * "United Kingdom", "Puerto Rico" — NOT ISO codes) to its ISO 3166-1 alpha-2
 * code for flagcdn.com raster flags. Raster images are used instead of emoji
 * flags because Windows browsers render flag emoji as bare letter pairs.
 *
 * Built against the ~92 distinct country values actually present in the data
 * (plus a few historical aliases). Anything unmapped returns '' so callers
 * gracefully fall back to text with no image.
 */
(function () {
  const ISO = {
    "Angola":"ao","Argentina":"ar","Australia":"au","Austria":"at","Azerbaijan":"az",
    "Bahrain":"bh","Belarus":"by","Belgium":"be","Bolivia":"bo","Bosnia":"ba",
    "Brazil":"br","Bulgaria":"bg","Burundi":"bi","Canada":"ca","Chile":"cl",
    "China":"cn","Colombia":"co","Croatia":"hr","Cyprus":"cy","Czech Republic":"cz",
    "Denmark":"dk","Dominican Republic":"do","Ecuador":"ec","Egypt":"eg","Estonia":"ee",
    "Finland":"fi","France":"fr","Georgia":"ge","Germany":"de","Greece":"gr",
    "Honduras":"hn","Hong Kong":"hk","Hungary":"hu","Iceland":"is","India":"in",
    "Indonesia":"id","Iran":"ir","Iraq":"iq","Ireland":"ie","Israel":"il",
    "Italy":"it","Ivory Coast":"ci","Japan":"jp","Jordan":"jo","Kazakhstan":"kz",
    "Kosovo":"xk","Kuwait":"kw","Latvia":"lv","Lebanon":"lb","Libya":"ly",
    "Lithuania":"lt","Luxembourg":"lu","Malaysia":"my","Mexico":"mx","Monaco":"mc",
    "Mongolia":"mn","Montenegro":"me","Morocco":"ma","Netherlands":"nl","New Zealand":"nz",
    "Nicaragua":"ni","Nigeria":"ng","North Macedonia":"mk","Philippines":"ph","Poland":"pl",
    "Portugal":"pt","Puerto Rico":"pr","Qatar":"qa","Romania":"ro","Russia":"ru",
    "Saudi Arabia":"sa","Serbia":"rs","Singapore":"sg","Slovakia":"sk","Slovenia":"si",
    "South Africa":"za","South Korea":"kr","Spain":"es","Sweden":"se","Switzerland":"ch",
    "Syria":"sy","Taiwan":"tw","Tanzania":"tz","Tunisia":"tn","Turkey":"tr",
    "UAE":"ae","USA":"us","Ukraine":"ua","United Kingdom":"gb","Uruguay":"uy",
    "Venezuela":"ve","Vietnam":"vn",
    // historical / alternate spellings that may appear
    "United Arab Emirates":"ae","England":"gb","Bosnia and Herzegovina":"ba",
    "Senegal":"sn","Thailand":"th","Andorra":"ad","South Sudan":"ss","Sudan":"sd"
  };

  // Nationality demonym -> country (a key into ISO above), covering every
  // country ISO maps. A player's Wikipedia |nationality= field is a demonym
  // ("American", "Spanish"), not a country name, and is a DIFFERENT concept
  // from birth_place/stint country (a player can be born in one country and
  // hold/represent another — e.g. Joel Embiid, born Cameroon, nationality
  // French). Where a country has more than one common English demonym
  // spelling, both are listed; all point at the exact same country/flag, so
  // this is not ambiguity. Genuinely coarser choices (documented, not
  // guesses): "Scottish"/"Welsh"/"Northern Irish" roll up to the dataset's
  // "United Kingdom" flag (no separate flags.js entries exist for the home
  // nations); "Korean" (unqualified) resolves to "South Korea" since no
  // "North Korea" entry exists in ISO. Nationalities for countries NOT in
  // ISO (e.g. "Cameroonian") are intentionally absent — silent no-op, not a
  // gap to fix here; expanding ISO's country coverage is future work.
  const DEMONYM = {
    "Angolan":"Angola",
    "Argentine":"Argentina","Argentinian":"Argentina",
    "Australian":"Australia",
    "Austrian":"Austria",
    "Azerbaijani":"Azerbaijan",
    "Bahraini":"Bahrain",
    "Belarusian":"Belarus","Belarusan":"Belarus",
    "Belgian":"Belgium",
    "Bolivian":"Bolivia",
    "Bosnian":"Bosnia",
    "Brazilian":"Brazil",
    "Bulgarian":"Bulgaria",
    "Burundian":"Burundi",
    "Canadian":"Canada",
    "Chilean":"Chile",
    "Chinese":"China",
    "Colombian":"Colombia",
    "Croatian":"Croatia",
    "Cypriot":"Cyprus",
    "Czech":"Czech Republic",
    "Danish":"Denmark",
    "Dominican":"Dominican Republic",
    "Ecuadorian":"Ecuador","Ecuadorean":"Ecuador",
    "Egyptian":"Egypt",
    "Estonian":"Estonia",
    "Finnish":"Finland",
    "French":"France",
    "Georgian":"Georgia",
    "German":"Germany",
    "Greek":"Greece",
    "Honduran":"Honduras",
    "Hongkonger":"Hong Kong","Hong Konger":"Hong Kong",
    "Hungarian":"Hungary",
    "Icelandic":"Iceland",
    "Indian":"India",
    "Indonesian":"Indonesia",
    "Iranian":"Iran",
    "Iraqi":"Iraq",
    "Irish":"Ireland",
    "Israeli":"Israel",
    "Italian":"Italy",
    "Ivorian":"Ivory Coast",
    "Japanese":"Japan",
    "Jordanian":"Jordan",
    "Kazakh":"Kazakhstan","Kazakhstani":"Kazakhstan",
    "Kosovar":"Kosovo","Kosovan":"Kosovo",
    "Kuwaiti":"Kuwait",
    "Latvian":"Latvia",
    "Lebanese":"Lebanon",
    "Libyan":"Libya",
    "Lithuanian":"Lithuania",
    "Luxembourgish":"Luxembourg","Luxembourger":"Luxembourg",
    "Malaysian":"Malaysia",
    "Mexican":"Mexico",
    "Monégasque":"Monaco","Monegasque":"Monaco","Monacan":"Monaco",
    "Mongolian":"Mongolia",
    "Montenegrin":"Montenegro",
    "Moroccan":"Morocco",
    "Dutch":"Netherlands",
    "New Zealander":"New Zealand","New Zealand":"New Zealand",
    "Nicaraguan":"Nicaragua",
    "Nigerian":"Nigeria",
    "Macedonian":"North Macedonia",
    "Filipino":"Philippines","Filipina":"Philippines","Philippine":"Philippines",
    "Polish":"Poland",
    "Portuguese":"Portugal",
    "Puerto Rican":"Puerto Rico",
    "Qatari":"Qatar",
    "Romanian":"Romania",
    "Russian":"Russia",
    "Saudi":"Saudi Arabia","Saudi Arabian":"Saudi Arabia",
    "Serbian":"Serbia",
    "Singaporean":"Singapore",
    "Slovak":"Slovakia",
    "Slovenian":"Slovenia","Slovene":"Slovenia",
    "South African":"South Africa",
    "South Korean":"South Korea","Korean":"South Korea",
    "Spanish":"Spain",
    "Swedish":"Sweden",
    "Swiss":"Switzerland",
    "Syrian":"Syria",
    "Taiwanese":"Taiwan",
    "Tanzanian":"Tanzania",
    "Tunisian":"Tunisia",
    "Turkish":"Turkey",
    "Emirati":"UAE",
    "American":"USA",
    "Ukrainian":"Ukraine",
    "British":"United Kingdom","Scottish":"United Kingdom",
    "Welsh":"United Kingdom","Northern Irish":"United Kingdom",
    "Uruguayan":"Uruguay",
    "Venezuelan":"Venezuela",
    "Vietnamese":"Vietnam",
    "English":"England",
    "Senegalese":"Senegal",
    "Thai":"Thailand",
    "Andorran":"Andorra",
    "South Sudanese":"South Sudan",
    "Sudanese":"Sudan"
  };

  const esc = s => String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

  // Returns an <img> flag for a country name, or '' if the country is unmapped
  // (graceful fallback — the caller keeps its text label). Uses flagcdn's plain
  // FLAT rectangular PNGs (the w20 variant, ~20px wide, crisp on hi-DPI) — not
  // waving/glossy flags.
  function flagImg(country) {
    const iso = ISO[country];
    if (!iso) return "";
    return `<img class="flag" src="https://flagcdn.com/w20/${iso}.png" ` +
           `width="16" height="12" loading="lazy" alt="${esc(country)} flag">`;
  }

  // Returns an <img> flag for a nationality demonym ("American", "Spanish"),
  // or '' if unmapped — a silent no-op, same contract as flagImg, so callers
  // never need to special-case a missing/unrecognized nationality.
  function nationalityFlag(nationality) {
    const country = DEMONYM[String(nationality ?? "").trim()];
    return country ? flagImg(country) : "";
  }

  window.COUNTRY_ISO = ISO;
  window.flagImg = flagImg;
  window.DEMONYM_TO_COUNTRY = DEMONYM;
  window.nationalityFlag = nationalityFlag;
})();
