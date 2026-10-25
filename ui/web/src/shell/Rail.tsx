import { useEffect, useRef } from "react";
import { Link, useLocation } from "react-router";

import { Icon } from "./Icon";
import styles from "./Rail.module.css";
import { FOOTER_SECTIONS, SECTIONS, type Section, sectionOf } from "./sections";

/** Колонка разделов слева; на узком экране — строка сверху. */
export function Rail() {
  const current = sectionOf(useLocation().pathname);
  const nav = useRef<HTMLElement>(null);
  // В строке на телефоне текущий раздел может оказаться за краем — ставим его в середину строки;
  // прокручивается только сама строка, страница остаётся на месте
  useEffect(() => {
    const row = nav.current;
    const link = row?.querySelector("[aria-current]");
    if (!row || !link || row.scrollWidth <= row.clientWidth) return;
    const offset = link.getBoundingClientRect().left - row.getBoundingClientRect().left;
    row.scrollLeft += offset - (row.clientWidth - link.clientWidth) / 2;
  }, [current]);
  const link = (section: Section) => (
    <Link
      key={section.path}
      to={section.path}
      className={styles.link}
      aria-label={section.label}
      title={section.label}
      aria-current={section === current ? "page" : undefined}
    >
      <Icon name={section.icon} />
    </Link>
  );
  return (
    <nav ref={nav} className={styles.rail} aria-label="Разделы">
      <Link to="/" className={styles.logo} aria-label="expsched">
        <Icon name="logo" size={22} />
      </Link>
      {SECTIONS.map(link)}
      <span className={styles.gap} />
      {FOOTER_SECTIONS.map(link)}
    </nav>
  );
}
