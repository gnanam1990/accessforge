/**
 * A table, which is a `<table>`.
 *
 * Grid layouts made of divs lose the one thing a table is for: a screen-reader user moving between
 * cells and hearing the row and column headers that give a value its meaning. So this renders real
 * table markup with a real `<caption>`, `scope="col"` on the header cells, and an optional row
 * header per row.
 *
 * Wide content scrolls inside its own container with `tabindex="0"`, because a scrollable region
 * that cannot be reached by keyboard is content that cannot be read without a mouse.
 *
 * There is no sorting, filtering or virtualization here. Those belong to the screens that need them
 * (modules 22–24) and each carries an accessibility obligation of its own — a sort control needs
 * `aria-sort` and an announcement, and virtualization needs the complete unvirtualized reading mode
 * UI-UX section 5 requires. Shipping half of either in the foundation would make the obligation
 * invisible.
 */

import type { JSX } from 'react'

import type { ReactNode } from 'react'

export interface Column<Row> {
  readonly key: string
  readonly header: string
  readonly cell: (row: Row) => ReactNode
  /** Marks this column as the row's header cell. At most one column should set it. */
  readonly isRowHeader?: boolean
}

export interface DataTableProps<Row> {
  /** Describes the table's contents. Required: a table announced with no caption is a grid of
   * numbers whose subject the reader has to infer. */
  readonly caption: string
  readonly columns: readonly Column<Row>[]
  readonly rows: readonly Row[]
  readonly rowKey: (row: Row) => string
}

export const DataTable = <Row,>({
  caption,
  columns,
  rows,
  rowKey,
}: DataTableProps<Row>): JSX.Element => (
  <div className="af-table-scroll" tabIndex={0} role="group" aria-label={caption}>
    <table className="af-table">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((column) => (
            <th key={column.key} scope="col">
              {column.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((column) =>
              column.isRowHeader === true ? (
                <th key={column.key} scope="row">
                  {column.cell(row)}
                </th>
              ) : (
                <td key={column.key}>{column.cell(row)}</td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)
