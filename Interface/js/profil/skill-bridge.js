/* Encart « Ajouter au profil », sous la description d'une offre : une compétence courante
   citée par l'offre, absente du profil.

   Le serveur n'extrait pas (encore) les compétences d'une offre : on cherche ici un petit
   vocabulaire de compétences répandues, mot entier, sans casse. C'est une aide, pas une
   analyse ; voir le plan (besoins côté API). Le bouton ouvre Profil › Compétences avec la
   compétence déjà saisie : l'utilisateur confirme d'un Entrée. */

import { el, icon } from '../core/dom.js';
import { navigate } from '../core/router.js';
import { state } from '../core/store.js';

// Volontairement sans les noms d'une lettre ou trop courants (« R », « Go ») : « R&D » ou
// un verbe anglais les feraient reconnaître à tort.
const VOCABULARY = [
  'Python', 'SQL', 'Java', 'JavaScript', 'TypeScript', 'C++', 'C#', 'Scala', 'PHP', 'Rust',
  'Excel', 'VBA', 'Power BI', 'Tableau', 'Looker', 'Qlik', 'SAS', 'SPSS', 'Stata', 'MATLAB',
  'Pandas', 'NumPy', 'scikit-learn', 'TensorFlow', 'PyTorch', 'Keras', 'Spark', 'PySpark', 'Hadoop',
  'Airflow', 'dbt', 'Kafka', 'Snowflake', 'Databricks', 'BigQuery', 'Redshift',
  'PostgreSQL', 'MySQL', 'MongoDB', 'Oracle', 'Elasticsearch',
  'AWS', 'Azure', 'GCP', 'Docker', 'Kubernetes', 'Terraform', 'Git', 'Linux', 'Jenkins',
  'React', 'Angular', 'Vue.js', 'Node.js', 'Django', 'Flask', 'FastAPI', 'Spring',
  'SAP', 'Salesforce', 'Jira', 'Figma', 'Agile', 'Scrum',
  'Machine Learning', 'Deep Learning', 'NLP', 'LLM', 'Computer Vision',
];

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const patterns = VOCABULARY.map(skill => ({
  skill,
  regex: new RegExp(`(^|[^\\p{L}\\p{N}+#])${escape(skill)}(?=$|[^\\p{L}\\p{N}+#])`, 'iu'),
}));

/* Compétences du vocabulaire citées par l'offre et absentes du profil (dans l'ordre du
   vocabulaire). */
function missingSkills(offer, profile) {
  const text = `${offer.titre}\n${offer.description || ''}`;
  // Le profil « connaît » une compétence s'il la cite, mot entier (« Power BI », ou
  // « Pandas / NumPy » pour NumPy).
  const known = [...profile.hard, ...profile.soft].join('\n');
  return patterns
    .filter(({ regex }) => regex.test(text) && !regex.test(known))
    .map(({ skill }) => skill);
}

/* Compétence à préremplir à l'ouverture de Profil › Compétences (consommée une fois). */
let prefill = '';
export function takeSkillPrefill() {
  const value = prefill;
  prefill = '';
  return value;
}

export function SkillBridge(offer) {
  if (!state.profile) return null;
  const missing = missingSkills(offer, state.profile);
  if (!missing.length) return null;
  const [first, ...others] = missing;
  return el('div', { class: 'aside-note' },
    el('p', { class: 'grow' },
      el('strong', {}, first), ' est demandé dans l’offre, mais absent de votre profil.',
      others.length > 0 && el('span', { class: 'field-hint' }, ` Aussi cités : ${others.slice(0, 4).join(', ')}.`)),
    el('button', {
      class: 'btn btn-profil btn-sm', type: 'button',
      onClick: () => { prefill = first; navigate('profil', 'competences'); },
    }, icon('plus'), 'Ajouter au profil'));
}
