// Channels and spawned threads.
module demo

fn produce(ch chan int, n int) {
	for i in 0 .. n {
		ch <- i
	}
	ch.close()
}

pub fn fanout(n int) int {
	ch := chan int{}
	spawn produce(ch, n)
	mut total := 0
	for {
		v := <-ch or { break }
		total += v
	}
	return total
}
