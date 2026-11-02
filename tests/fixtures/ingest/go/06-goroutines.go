// Goroutines, channels and select.
package demo

func produce(out chan<- int, n int) {
	for i := 0; i < n; i++ {
		out <- i
	}
	close(out)
}

func consume(in <-chan int) int {
	total := 0
	for v := range in {
		total += v
	}
	return total
}

func fanout(n int) int {
	ch := make(chan int)
	go produce(ch, n)
	return consume(ch)
}
