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

  const esc = s => String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");

  // Returns an <img> flag for a country name, or '' if the country is unmapped
  // (graceful fallback — the caller keeps its text label).
  function flagImg(country) {
    const iso = ISO[country];
    if (!iso) return "";
    return `<img class="flag" src="https://flagcdn.com/16x12/${iso}.png" ` +
           `width="16" height="12" loading="lazy" alt="${esc(country)} flag">`;
  }

  window.COUNTRY_ISO = ISO;
  window.flagImg = flagImg;
})();
