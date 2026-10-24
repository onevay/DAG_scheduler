import { Icon } from "./Icon";
import styles from "./Rail.module.css";
import { FOOTER_SECTIONS, SECTIONS, type Section, sectionOf } from "./sections";

/** Колонка разделов слева; на узком экране — строка сверху. */
export function Rail({ pathname }: { pathname: string }) {
  const current = sectionOf(pathname);
  const link = (section: Section) => (
    <a
      key={section.path}
      href={section.path}
      className={styles.link}
      aria-label={section.label}
      title={section.label}
      aria-current={section === current ? "page" : undefined}
    >
      <Icon name={section.icon} />
    </a>
  );
  return (
    <nav className={styles.rail} aria-label="Разделы">
      <a href="/" className={styles.logo} aria-label="expsched">
        <Icon name="logo" size={22} />
      </a>
      {SECTIONS.map(link)}
      <span className={styles.gap} />
      {FOOTER_SECTIONS.map(link)}
    </nav>
  );
}
