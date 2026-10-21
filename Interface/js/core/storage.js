/* Préférences du poste (localStorage). Commodités seulement, jamais des données : le
   stockage peut être indisponible (navigation privée) ou contenir des valeurs périmées,
   et tout ce qui est relu doit être revalidé par l'appelant. */

const PREFIX = 'jobtool.';

export function readPref(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch (err) {
    return fallback;
  }
}

export function writePref(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch (err) {
    /* stockage indisponible : la préférence ne sera simplement pas retenue */
  }
}
