/* Translation-unit structure: statics, externs, forward decls. */
static int counter = 0;
extern int shared_total;

static int bump(void) { return ++counter; }

int next(void);
int next(void) { return bump(); }

int peek(void) { return counter; }
