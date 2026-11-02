// Generic functions and structs.
module demo

interface Identifiable {
	id() int
}

struct Account {
	user_id int
}

fn (a Account) id() int {
	return a.user_id
}

pub fn identity(value int) int {
	return value
}

fn use_account() int {
	a := Account{user_id: 7}
	return a.id()
}
