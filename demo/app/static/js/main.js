var host='http://127.0.0.1:5000/';


function renderSiteStatus(siteStatus){
  var container = $('#site-status-div');
  container.empty();
  if(!siteStatus || !siteStatus.length){
    return;
  }
  for(var i=0; i<siteStatus.length; i++){
    var s = siteStatus[i];
    var cls, msg;
    if(s.status === 'failed'){
      cls = 'site-status-failed';
      msg = s.site + ': could not be scraped' + (s.reason ? ' (' + s.reason + ')' : '');
    }
    else if(s.status === 'zero_results'){
      cls = 'site-status-zero';
      msg = s.site + ': no products found for this search';
    }
    else{
      cls = 'site-status-success';
      msg = s.site + ': ' + s.count + ' product(s) found';
    }
    container.append($('<div/>').addClass('site-status ' + cls).text(msg));
  }
}

function DataIsReady(response){
  var prof = $('.product-info').clone();
  $("#products-div").empty();
  renderSiteStatus(response['site_status']);
  len=response['product_name'].length;
  for(var i=0; i<len;i++){
     var prof_card = prof.clone();
     prof_card.find('.product_name').text(response['product_name'][i]);
     prof_card.find('.price').text(response['price'][i]);
     prof_card.find('.source').text(response['source'][i]);
     prof_card.find('.product_image').attr('src',response['image_url'][i]);
     prof_card.find('.product_url').attr('href',response['product_url'][i]);
     $("#products-div").append(prof_card);

  }
        
}


$(document).ready(function(){
  $('#DivId2').hide()
	$('#search-button').click(function(){
   
    var val = $('#search-keyword').val();
    console.log(val);
    if(val && val.length>0){
      $.post({
      url: host+'search_results',
      type: 'POST',
      data: JSON.stringify({ 'product': val}) ,
      contentType: 'application/json; charset=UTF-8',
      success: function (response) {
        console.log(response);
        DataIsReady(response);
        $('#DivId1').hide()
        $('#DivId2').show()
          
      },
      error: function () {
          console.log('Error in search')
          // alert("error");
      }
      });  
    }
    else{
      console.log('Search Keyword Empty');
    }
	});

  $('#home').click(function(){
    $('#DivId2').hide();
    $('#search-keyword').val('');
    $('#DivId1').show();


  });
});