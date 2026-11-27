"""Create a ZIP without reading all submission sources into memory."""

from zipfile import ZIP_DEFLATED, ZipFile


class ZipChunks:
    def __init__(self):
        self.position = 0
        self.parts = []

    def write(self, data):
        self.parts.append(data)
        self.position += len(data)
        return len(data)

    def tell(self):
        return self.position

    def flush(self):
        pass

    def drain(self):
        parts, self.parts = self.parts, []
        return parts


def submission_bytes(row):
    if row["source_bytes"] is not None:
        return row["source_bytes"]
    return row["source"].encode("utf-8")


def packed_zip(entries):
    output = ZipChunks()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=3) as archive:
        for name, data in entries:
            if isinstance(data, bytes):
                archive.writestr(name, data)
            else:
                with archive.open(name, "w") as member:
                    for chunk in data:
                        member.write(chunk)
                        yield from output.drain()
            yield from output.drain()
    yield from output.drain()


def submission_zip(pool, ids):
    with pool.connection() as conn, conn.cursor(name="submissions_zip") as cursor:
        cursor.itersize = 16
        cursor.execute(
            """SELECT s.id, s.filename, s.source, s.source_bytes, u.username
               FROM submissions s JOIN users u ON u.id = s.student_id
               WHERE s.id = ANY(%s) ORDER BY u.username, s.id""",
            (ids,),
        )

        def entries():
            for row in cursor:
                filename = row["filename"].replace("\\", "/").rsplit("/", 1)[-1]
                if filename in {"", ".", ".."}:
                    filename = "entrega.txt"
                yield f"{row['username']}/{row['id']}/{filename}", submission_bytes(row)

        yield from packed_zip(entries())
