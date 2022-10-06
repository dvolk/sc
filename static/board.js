const draggables = document.querySelectorAll(".draggable")
const containers = document.querySelectorAll(".container")
var target = null

draggables.forEach(draggable => {
  draggable.addEventListener('dragstart', () => {
    draggable.classList.add('dragging')
  })
})

draggables.forEach(draggable => {
  draggable.addEventListener('dragend', () => {
    draggable.classList.remove('dragging')
    target.appendChild(draggable)
    const url = '/item/move/' + draggable.id + '/' + target.id
    console.log(url)
    var xmlhttp = new XMLHttpRequest();
    xmlhttp.open("GET", url);
    xmlhttp.send()
  })
})

containers.forEach(container => {
  container.addEventListener('dragover', () => {
    target = container
  })
})
