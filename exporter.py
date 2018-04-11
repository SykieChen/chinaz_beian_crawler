#!/usr/bin/python3
# coding=utf-8

import xlrd
import logging
import requests
import sys
import time
import multiprocessing.dummy
from dbio import DBIO
from lxml import etree


class Exporter:
    def __init__(self, dbio, threads, start_date, end_date):
        self.logger = logging.getLogger("cnzz_crawler.exporter")
        self.baseurl = 'http://icp.chinaz.com/saveExc.ashx'
        self.queryurl = 'http://icp.chinaz.com/conditions'
        self.db = dbio
        self.threads = threads
        self.start_date = str(start_date)
        self.end_date = str(end_date)
        self.session = requests.Session()
        self.session.mount(
            'http://', requests.adapters.HTTPAdapter(max_retries=3))

    def write_data(self, rows):
        count = 0
        for index in range(0, len(rows)):
            if index % 100 == 0:
                print('\rWritting %.2f %%, %d of %d' %
                      (100*(index+1)/len(rows), index+1, len(rows)))
        for row in rows:
            try:
                self.db.write(row)
                count += 1
            except:
                pass
        return count

    def analyse_xls(self, xls_data, date):
        xls = xlrd.open_workbook(file_contents=xls_data)
        x_table = xls.sheets()[0]

        if x_table.nrows > 1:
            ret = []
            for i in range(2, x_table.nrows):
                ret.append(x_table.row_values(i))
            self.total += len(ret)
            print('%s returned %d results, %d in total.' % (
                date, len(ret), self.total))
            return ret
        else:
            print('%s returned no results.' % (date), end='\r')
            return []

    def analyse_xpath(self, html, date):
        selector = etree.HTML(html)
        # get total page
        total_pages = int(selector.xpath(
            '//div[@id="pagelist"]/span[1]/text()')[0][1:-4])
        # get data
        rows = []
        trs = selector.xpath('//tbody[@id="result_table"]/tr')
        for tr in trs:
            row = ['id_place_holder']
            # domain
            row.append(tr.xpath('td[1]/a/text()')[0])
            # owner_name
            row.append(tr.xpath('td[2]/text()')[0])
            # owner_type
            row.append(tr.xpath('td[3]/text()')[0])
            # icp_cert
            row.append(tr.xpath('td[4]/text()')[0])
            # site_name
            row.append(tr.xpath('td[5]/text()')[0])
            # homepage
            row.append(' '.join(tr.xpath('td[6]/span/a/text()')))
            # time
            row.append(tr.xpath('td[7]/text()')[0])
            rows.append(row)
            self.logger.debug(row)
        return total_pages, rows

    def fetch(self, start_str_time, province):
        get_para = {
            '_host': '',
            '_companyName': '',
            '_companyXZ': '不限',
            '_wname': '',
            '_provinces': province,
            '_btime': start_str_time,
            '_etime': start_str_time,
            '_page': '',
            'saveData': '导出所有结果'
        }
        # print('\rProcessing %s' % start_str_time)
        # retry
        retry = 0
        while retry < 5:
            try:
                ret_xls = self.session.post(
                    self.baseurl, data=get_para).content
                ret_data = self.analyse_xls(ret_xls, start_str_time)
                if len(ret_data) > 999:
                    # more than 1000, possibly lose data
                    self.logger.warning(
                        'More than 1000 results, possible data loss, using web page fetch.')
                    ret_data = self.fetch_webpage(start_str_time, province)
                retry = 99
            except:
                self.logger.warning('Network error, retrying %d' % retry)
                retry += 1
        if retry < 99:
            self.logger.error('Network error, give up')
            return []
        else:
            return ret_data

    def fetch_webpage(self, start_str_time, province):
        # when there is over 1000 results in a day, xls file contains only the first 1000 data
        # so we have to use web page to grab data up to 50 pages (2500 data).
        get_para = {
            'companyName': '',
            'webName': '',
            'companyXZ': '不限',
            'provinces': province,
            'btime': start_str_time,
            'etime': start_str_time,
            'page': 1
        }
        # get pages, no more than 50
        max_page = 50
        ret_data = []
        while get_para['page'] <= max_page and get_para['page'] <= 50:
            # retry
            retry = 0
            while retry < 5:
                try:
                    html = self.session.get(
                        self.queryurl, params=get_para).text
                    max_page, page_data = self.analyse_xpath(
                        html, start_str_time)
                    retry = 99
                except:
                    self.logger.warning('Network error, retrying %d' % retry)
                    retry += 1
            if retry < 99:
                self.logger.error(
                    'Network error, give up page %d' % get_para['page'])
            else:
                ret_data += page_data
                print('\r%s page %d of %d returned %d results, %d in total.' % (
                    start_str_time, get_para['page'], max_page, len(page_data), len(ret_data)))
            get_para['page'] += 1
        return ret_data

    def get_province(self, province):
        self.logger.info('Getting province of %s' % province)
        # date must be YYYYMMDD
        try:
            start_asc_time = time.mktime(time.strptime(self.start_date, '%Y%m%d'))
            end_asc_time = time.mktime(time.strptime(self.end_date, '%Y%m%d'))
        except:
            self.logger.error('Time format error! %s to %s' % (self.start_date, self.end_date))
            return
        thread_pool = multiprocessing.dummy.Pool(processes=self.threads)
        self.logger.debug('%d processes' % self.threads)
        results = []
        self.total = 0
        while start_asc_time <= end_asc_time:
            start_str_time = time.strftime(
                '%Y-%m-%d', time.localtime(start_asc_time))
            results.append(thread_pool.apply_async(
                self.fetch, args=(start_str_time, province, )))
            # add one day to time
            start_asc_time += (3600*24)
        thread_pool.close()
        thread_pool.join()
        domain_data = []
        for result in results:
            domain_data += result.get()
        self.logger.info('Got %d results from %s' %
                         (len(domain_data), province))
        self.logger.info('Writting to database')
        written_count = self.write_data(domain_data)
        self.logger.info('%d results written' % (written_count))
